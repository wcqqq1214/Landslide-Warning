"""Independent append-only SQLite ledger for Ootang calibration shadows.

This module deliberately does not import or extend the E2-A live ledger.  It has
its own SQLite application id, schema identity, event allowlist, and hash chain.
Every write is one ``BEGIN IMMEDIATE`` transaction, and every public read first
verifies the exact schema and the complete canonical-JSON SHA-256 chain.

The hash chain detects mutation but does not authenticate a writer or provide a
trusted timestamp.  Those evidence boundaries belong to the shadow protocol
that consumes this storage primitive.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import re
import sqlite3
from typing import Any


ZERO_HASH = "0" * 64
SCHEMA_VERSION = 1
APPLICATION_ID = 0x4F435348  # ASCII "OCSH" (Ootang Calibration SHadow).

ALLOWED_EVENT_TYPES = frozenset(
    {
        "shadow_epoch_genesis",
        "shadow_backfill_ineligible",
        "shadow_issue_batch_opened",
        "shadow_candidate_issued",
        "shadow_issue_batch_sealed",
        "shadow_outcome_batch_opened",
        "shadow_candidate_revealed",
        "shadow_state_updated",
        "shadow_outcome_batch_settled",
        "shadow_outcome_revision_rescored",
        "shadow_integrity_blocked",
        "shadow_epoch_closed",
    }
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_RECORDED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

_HASH_COLUMNS = (
    "protocol_config_sha256",
    "code_sha256",
    "environment_sha256",
    "input_manifest_sha256",
    "model_manifest_sha256",
    "state_before_sha256",
    "state_after_sha256",
)

_EVENT_COLUMNS = (
    "sequence_id",
    "event_key",
    "event_type",
    "recorded_at_utc",
    "transaction_sha256",
    "transaction_position",
    "transaction_size",
    "target_date",
    "station",
    "issue_id",
    *_HASH_COLUMNS,
    "payload_json",
    "previous_entry_sha256",
    "entry_sha256",
)

_TABLE_SQL = """CREATE TABLE events (
    sequence_id INTEGER PRIMARY KEY NOT NULL,
    event_key TEXT NOT NULL CHECK(length(event_key) > 0),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'shadow_epoch_genesis',
        'shadow_backfill_ineligible',
        'shadow_issue_batch_opened',
        'shadow_candidate_issued',
        'shadow_issue_batch_sealed',
        'shadow_outcome_batch_opened',
        'shadow_candidate_revealed',
        'shadow_state_updated',
        'shadow_outcome_batch_settled',
        'shadow_outcome_revision_rescored',
        'shadow_integrity_blocked',
        'shadow_epoch_closed'
    )),
    recorded_at_utc TEXT NOT NULL,
    transaction_sha256 TEXT NOT NULL,
    transaction_position INTEGER NOT NULL,
    transaction_size INTEGER NOT NULL,
    target_date TEXT,
    station TEXT,
    issue_id TEXT,
    protocol_config_sha256 TEXT NOT NULL,
    code_sha256 TEXT NOT NULL,
    environment_sha256 TEXT NOT NULL,
    input_manifest_sha256 TEXT NOT NULL,
    model_manifest_sha256 TEXT NOT NULL,
    state_before_sha256 TEXT NOT NULL,
    state_after_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    previous_entry_sha256 TEXT NOT NULL,
    entry_sha256 TEXT NOT NULL
) STRICT"""

_EVENT_KEY_INDEX_SQL = (
    "CREATE UNIQUE INDEX events_event_key_unique ON events(event_key)"
)
_ENTRY_HASH_INDEX_SQL = (
    "CREATE UNIQUE INDEX events_entry_sha256_unique ON events(entry_sha256)"
)
_NO_CONFLICTING_INSERT_TRIGGER_SQL = """CREATE TRIGGER events_no_conflicting_insert
BEFORE INSERT ON events
WHEN EXISTS (
    SELECT 1 FROM events
    WHERE sequence_id = NEW.sequence_id
       OR event_key = NEW.event_key
       OR entry_sha256 = NEW.entry_sha256
)
BEGIN
    SELECT RAISE(ABORT, 'shadow events ledger rejects conflicting insert or replace');
END"""
_NO_UPDATE_TRIGGER_SQL = """CREATE TRIGGER events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'shadow events ledger is append-only');
END"""
_NO_DELETE_TRIGGER_SQL = """CREATE TRIGGER events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'shadow events ledger is append-only');
END"""

_SCHEMA_SQL = {
    ("table", "events"): _TABLE_SQL,
    ("index", "events_event_key_unique"): _EVENT_KEY_INDEX_SQL,
    ("index", "events_entry_sha256_unique"): _ENTRY_HASH_INDEX_SQL,
    (
        "trigger",
        "events_no_conflicting_insert",
    ): _NO_CONFLICTING_INSERT_TRIGGER_SQL,
    ("trigger", "events_no_update"): _NO_UPDATE_TRIGGER_SQL,
    ("trigger", "events_no_delete"): _NO_DELETE_TRIGGER_SQL,
}


class LedgerError(RuntimeError):
    """Base error for fail-closed shadow-ledger operations."""


class LedgerValidationError(LedgerError):
    """A proposed event is not canonical or complete."""


class LedgerConflictError(LedgerError):
    """An event-key retry is partial or changes stable content."""


class LedgerSchemaError(LedgerError):
    """The database identity, schema, indexes, or triggers changed."""


class LedgerIntegrityError(LedgerError):
    """Stored events do not reproduce the complete hash chain."""


@dataclass(frozen=True)
class EventSpec:
    """Stable caller-controlled content for one shadow event.

    Sequence id, recorded time, predecessor hash, and entry hash are assigned by
    the ledger while it holds the write transaction.
    """

    event_key: str
    event_type: str
    target_date: str | None
    station: str | None
    issue_id: str | None
    protocol_config_sha256: str
    code_sha256: str
    environment_sha256: str
    input_manifest_sha256: str
    model_manifest_sha256: str
    state_before_sha256: str
    state_after_sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class LedgerEvent:
    """One fully assigned, chain-bound shadow ledger event."""

    sequence_id: int
    event_key: str
    event_type: str
    recorded_at_utc: str
    transaction_sha256: str
    transaction_position: int
    transaction_size: int
    target_date: str | None
    station: str | None
    issue_id: str | None
    protocol_config_sha256: str
    code_sha256: str
    environment_sha256: str
    input_manifest_sha256: str
    model_manifest_sha256: str
    state_before_sha256: str
    state_after_sha256: str
    payload: dict[str, Any]
    previous_entry_sha256: str
    entry_sha256: str


@dataclass(frozen=True)
class _PreparedSpec:
    spec: EventSpec
    payload_json: str


def _normalized_sql(value: str) -> str:
    return " ".join(value.split())


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"forbidden JSON constant: {value}")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_payload_json(
    payload: Mapping[str, Any],
    *,
    error_type: type[LedgerError],
) -> str:
    if not isinstance(payload, Mapping):
        raise error_type("event payload must be a JSON object")
    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        encoded.encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise error_type("event payload is not finite canonical JSON") from exc
    return encoded


def _decode_stored_payload(payload_json: object) -> dict[str, Any]:
    if not isinstance(payload_json, str):
        raise LedgerIntegrityError("stored payload_json is not text")
    try:
        payload = json.loads(
            payload_json,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise LedgerIntegrityError("stored payload_json is invalid") from exc
    if not isinstance(payload, dict):
        raise LedgerIntegrityError("stored payload_json is not an object")
    canonical = _canonical_payload_json(payload, error_type=LedgerIntegrityError)
    if canonical != payload_json:
        raise LedgerIntegrityError("stored payload_json is not canonical")
    return payload


def _validate_text(
    value: object,
    name: str,
    *,
    optional: bool = False,
    error_type: type[LedgerError] = LedgerValidationError,
) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value or value.strip() != value:
        raise error_type(f"{name} must be non-empty canonical text")
    if "\x00" in value:
        raise error_type(f"{name} must not contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise error_type(f"{name} must be valid UTF-8 text") from exc


def _validate_target_date(
    value: object,
    *,
    error_type: type[LedgerError] = LedgerValidationError,
) -> None:
    if value is None:
        return
    _validate_text(value, "target_date", error_type=error_type)
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise error_type("target_date must be canonical YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise error_type("target_date must be canonical YYYY-MM-DD")


def _validate_hash(
    value: object,
    name: str,
    *,
    error_type: type[LedgerError] = LedgerValidationError,
) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise error_type(f"{name} must be a lowercase SHA-256 hex digest")


def _validate_recorded_at(value: object) -> None:
    if not isinstance(value, str) or _RECORDED_AT_RE.fullmatch(value) is None:
        raise LedgerIntegrityError("recorded_at_utc is not canonical UTC text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise LedgerIntegrityError("recorded_at_utc is not a valid UTC time") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
        raise LedgerIntegrityError("recorded_at_utc is not canonical UTC text")


def _prepare_spec(spec: EventSpec) -> _PreparedSpec:
    if not isinstance(spec, EventSpec):
        raise LedgerValidationError("append_transaction accepts EventSpec values")
    _validate_text(spec.event_key, "event_key")
    if spec.event_type not in ALLOWED_EVENT_TYPES:
        raise LedgerValidationError(f"unknown event_type: {spec.event_type!r}")
    _validate_target_date(spec.target_date)
    _validate_text(spec.station, "station", optional=True)
    _validate_text(spec.issue_id, "issue_id", optional=True)
    for name in _HASH_COLUMNS:
        _validate_hash(getattr(spec, name), name)
    return _PreparedSpec(
        spec=spec,
        payload_json=_canonical_payload_json(
            spec.payload, error_type=LedgerValidationError
        ),
    )


def _stable_transaction_record(
    *, spec: EventSpec, payload: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "event_key": spec.event_key,
        "event_type": spec.event_type,
        "target_date": spec.target_date,
        "station": spec.station,
        "issue_id": spec.issue_id,
        "protocol_config_sha256": spec.protocol_config_sha256,
        "code_sha256": spec.code_sha256,
        "environment_sha256": spec.environment_sha256,
        "input_manifest_sha256": spec.input_manifest_sha256,
        "model_manifest_sha256": spec.model_manifest_sha256,
        "state_before_sha256": spec.state_before_sha256,
        "state_after_sha256": spec.state_after_sha256,
        "payload": dict(payload),
    }


def _transaction_sha256(prepared: tuple[_PreparedSpec, ...]) -> str:
    records = [
        _stable_transaction_record(
            spec=item.spec,
            payload=_decode_stored_payload(item.payload_json),
        )
        for item in prepared
    ]
    try:
        encoded = json.dumps(
            records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (
        RecursionError,
        TypeError,
        ValueError,
        UnicodeEncodeError,
    ) as exc:  # pragma: no cover
        raise LedgerValidationError("shadow transaction is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _stored_transaction_sha256(events: tuple[LedgerEvent, ...]) -> str:
    prepared = tuple(
        _PreparedSpec(
            spec=EventSpec(
                event_key=event.event_key,
                event_type=event.event_type,
                target_date=event.target_date,
                station=event.station,
                issue_id=event.issue_id,
                protocol_config_sha256=event.protocol_config_sha256,
                code_sha256=event.code_sha256,
                environment_sha256=event.environment_sha256,
                input_manifest_sha256=event.input_manifest_sha256,
                model_manifest_sha256=event.model_manifest_sha256,
                state_before_sha256=event.state_before_sha256,
                state_after_sha256=event.state_after_sha256,
                payload=event.payload,
            ),
            payload_json=_canonical_payload_json(
                event.payload, error_type=LedgerIntegrityError
            ),
        )
        for event in events
    )
    try:
        return _transaction_sha256(prepared)
    except LedgerValidationError as exc:  # pragma: no cover - rows already validated
        raise LedgerIntegrityError(
            "stored shadow transaction is not canonical"
        ) from exc


def _canonical_entry_json(event: LedgerEvent) -> str:
    envelope = {
        "sequence_id": event.sequence_id,
        "event_key": event.event_key,
        "event_type": event.event_type,
        "recorded_at_utc": event.recorded_at_utc,
        "transaction_sha256": event.transaction_sha256,
        "transaction_position": event.transaction_position,
        "transaction_size": event.transaction_size,
        "target_date": event.target_date,
        "station": event.station,
        "issue_id": event.issue_id,
        "protocol_config_sha256": event.protocol_config_sha256,
        "code_sha256": event.code_sha256,
        "environment_sha256": event.environment_sha256,
        "input_manifest_sha256": event.input_manifest_sha256,
        "model_manifest_sha256": event.model_manifest_sha256,
        "state_before_sha256": event.state_before_sha256,
        "state_after_sha256": event.state_after_sha256,
        "payload": event.payload,
        "previous_entry_sha256": event.previous_entry_sha256,
    }
    try:
        return json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (
        RecursionError,
        TypeError,
        ValueError,
    ) as exc:  # pragma: no cover - payload prevalidated
        raise LedgerIntegrityError("event envelope is not canonical JSON") from exc


def _entry_sha256(event: LedgerEvent) -> str:
    try:
        encoded = _canonical_entry_json(event).encode("utf-8")
    except UnicodeEncodeError as exc:  # pragma: no cover - text fields prevalidated
        raise LedgerIntegrityError("event envelope is not valid UTF-8") from exc
    return hashlib.sha256(encoded).hexdigest()


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class AppendOnlyLedger:
    """Single-writer, idempotent, fully verified shadow event ledger."""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        try:
            self.path = Path(path)
        except (TypeError, ValueError) as exc:
            raise LedgerValidationError("ledger path must be path-like") from exc
        if str(path) == ":memory:":
            raise LedgerValidationError("the ledger requires a durable file path")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, Real):
            raise LedgerValidationError(
                "timeout_seconds must be a finite positive real number"
            )
        try:
            checked_timeout = float(timeout_seconds)
        except (OverflowError, TypeError, ValueError) as exc:
            raise LedgerValidationError(
                "timeout_seconds must be a finite positive real number"
            ) from exc
        if not math.isfinite(checked_timeout) or checked_timeout <= 0.0:
            raise LedgerValidationError(
                "timeout_seconds must be a finite positive real number"
            )
        self.timeout_seconds = checked_timeout
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LedgerSchemaError(
                "shadow ledger parent directory cannot be prepared"
            ) from exc
        self._initialize()

    def _database_kind(self, connection: sqlite3.Connection) -> str:
        """Classify an open database without changing persistent state."""

        try:
            application_id = int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            )
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            objects = connection.execute(
                "SELECT type, name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerSchemaError(
                "cannot inspect shadow ledger identity safely"
            ) from exc
        if application_id == 0 and user_version == 0 and not objects:
            return "fresh"
        if application_id != APPLICATION_ID or user_version != SCHEMA_VERSION:
            raise LedgerSchemaError(
                "database is not an Ootang calibration-shadow ledger"
            )
        self._verify_schema(connection)
        return "owned"

    def _preflight_existing_database(self) -> None:
        """Reject a foreign database through a query-only connection."""

        try:
            if not self.path.exists() or self.path.stat().st_size == 0:
                return
        except OSError as exc:
            raise LedgerSchemaError(
                "shadow ledger metadata cannot be inspected"
            ) from exc

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro",
                timeout=self.timeout_seconds,
                isolation_level=None,
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise LedgerSchemaError(
                    "shadow ledger identity preflight is not query-only"
                )
            self._database_kind(connection)
        except LedgerError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise LedgerSchemaError(
                "cannot inspect existing shadow ledger read-only"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def _connect(self) -> sqlite3.Connection:
        self._preflight_existing_database()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self.timeout_seconds,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            # Recheck after opening read-write and before the first persistent
            # PRAGMA.  Foreign databases must never be adopted or journal-mutated.
            self._database_kind(connection)
            connection.execute(
                f"PRAGMA busy_timeout={int(self.timeout_seconds * 1000)}"
            )
            mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
            if mode.lower() != "wal":
                mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0])
            if mode.lower() != "wal":
                raise LedgerSchemaError("shadow ledger could not enter WAL mode")
            connection.execute("PRAGMA synchronous=FULL")
            synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
            if synchronous != 2:
                raise LedgerSchemaError("shadow ledger is not synchronous=FULL")
            return connection
        except LedgerError:
            if connection is not None:
                connection.close()
            raise
        except (OverflowError, OSError, sqlite3.Error, ValueError) as exc:
            if connection is not None:
                connection.close()
            raise LedgerSchemaError("cannot open shadow ledger safely") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            objects = connection.execute(
                "SELECT type, name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if not objects:
                application_id = int(
                    connection.execute("PRAGMA application_id").fetchone()[0]
                )
                user_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if application_id != 0 or user_version != 0:
                    raise LedgerSchemaError(
                        "empty database has unexpected schema identity"
                    )
                for sql in _SCHEMA_SQL.values():
                    connection.execute(sql)
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._verify_schema(connection)
            self._validate_chain_locked(connection)
            connection.commit()
        except LedgerError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise LedgerSchemaError(
                "shadow ledger schema initialization failed"
            ) from exc
        finally:
            connection.close()

    def _verify_schema(self, connection: sqlite3.Connection) -> None:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if application_id != APPLICATION_ID or user_version != SCHEMA_VERSION:
            raise LedgerSchemaError("shadow ledger schema identity/version mismatch")

        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        actual_keys = {(row["type"], row["name"]) for row in rows}
        if actual_keys != set(_SCHEMA_SQL):
            raise LedgerSchemaError(
                "shadow ledger has missing or unknown schema objects"
            )
        for row in rows:
            key = (row["type"], row["name"])
            if row["tbl_name"] != "events":
                raise LedgerSchemaError("shadow schema object targets unknown table")
            if not isinstance(row["sql"], str) or _normalized_sql(
                row["sql"]
            ) != _normalized_sql(_SCHEMA_SQL[key]):
                raise LedgerSchemaError(
                    f"shadow ledger schema SQL drifted for {row['name']}"
                )

        columns = connection.execute("PRAGMA table_xinfo(events)").fetchall()
        names = [row["name"] for row in columns]
        if names != list(_EVENT_COLUMNS) or len(names) != len(set(names)):
            raise LedgerSchemaError(
                "shadow events table has missing, duplicate, or unknown columns"
            )
        expected_required = {
            "sequence_id",
            "event_key",
            "event_type",
            "recorded_at_utc",
            "transaction_sha256",
            "transaction_position",
            "transaction_size",
            *_HASH_COLUMNS,
            "payload_json",
            "previous_entry_sha256",
            "entry_sha256",
        }
        for row in columns:
            expected_type = (
                "INTEGER"
                if row["name"]
                in {"sequence_id", "transaction_position", "transaction_size"}
                else "TEXT"
            )
            expected_pk = 1 if row["name"] == "sequence_id" else 0
            expected_not_null = 1 if row["name"] in expected_required else 0
            if (
                row["type"] != expected_type
                or row["pk"] != expected_pk
                or row["notnull"] != expected_not_null
                or row["dflt_value"] is not None
                or row["hidden"] != 0
            ):
                raise LedgerSchemaError(
                    f"shadow events column contract drifted for {row['name']}"
                )
        table_record = next(
            (
                row
                for row in connection.execute("PRAGMA table_list").fetchall()
                if row["schema"] == "main" and row["name"] == "events"
            ),
            None,
        )
        if (
            table_record is None
            or table_record["type"] != "table"
            or table_record["ncol"] != len(_EVENT_COLUMNS)
            or table_record["wr"] != 0
            or table_record["strict"] != 1
        ):
            raise LedgerSchemaError("shadow events is not the expected STRICT table")

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> LedgerEvent:
        sequence_id = row["sequence_id"]
        if (
            not isinstance(sequence_id, int)
            or isinstance(sequence_id, bool)
            or sequence_id < 1
        ):
            raise LedgerIntegrityError("sequence_id is not a positive integer")
        _validate_text(row["event_key"], "event_key", error_type=LedgerIntegrityError)
        if row["event_type"] not in ALLOWED_EVENT_TYPES:
            raise LedgerIntegrityError("stored shadow event_type is unknown")
        _validate_recorded_at(row["recorded_at_utc"])
        _validate_hash(
            row["transaction_sha256"],
            "transaction_sha256",
            error_type=LedgerIntegrityError,
        )
        transaction_position = row["transaction_position"]
        transaction_size = row["transaction_size"]
        if (
            not isinstance(transaction_position, int)
            or isinstance(transaction_position, bool)
            or not isinstance(transaction_size, int)
            or isinstance(transaction_size, bool)
            or transaction_position < 1
            or transaction_size < 1
            or transaction_position > transaction_size
        ):
            raise LedgerIntegrityError(
                "stored shadow transaction position/size is invalid"
            )
        _validate_target_date(row["target_date"], error_type=LedgerIntegrityError)
        _validate_text(
            row["station"],
            "station",
            optional=True,
            error_type=LedgerIntegrityError,
        )
        _validate_text(
            row["issue_id"],
            "issue_id",
            optional=True,
            error_type=LedgerIntegrityError,
        )
        for name in _HASH_COLUMNS:
            _validate_hash(row[name], name, error_type=LedgerIntegrityError)
        _validate_hash(
            row["previous_entry_sha256"],
            "previous_entry_sha256",
            error_type=LedgerIntegrityError,
        )
        _validate_hash(
            row["entry_sha256"],
            "entry_sha256",
            error_type=LedgerIntegrityError,
        )
        payload = _decode_stored_payload(row["payload_json"])
        return LedgerEvent(
            sequence_id=sequence_id,
            event_key=row["event_key"],
            event_type=row["event_type"],
            recorded_at_utc=row["recorded_at_utc"],
            transaction_sha256=row["transaction_sha256"],
            transaction_position=transaction_position,
            transaction_size=transaction_size,
            target_date=row["target_date"],
            station=row["station"],
            issue_id=row["issue_id"],
            protocol_config_sha256=row["protocol_config_sha256"],
            code_sha256=row["code_sha256"],
            environment_sha256=row["environment_sha256"],
            input_manifest_sha256=row["input_manifest_sha256"],
            model_manifest_sha256=row["model_manifest_sha256"],
            state_before_sha256=row["state_before_sha256"],
            state_after_sha256=row["state_after_sha256"],
            payload=payload,
            previous_entry_sha256=row["previous_entry_sha256"],
            entry_sha256=row["entry_sha256"],
        )

    def _validate_chain_locked(
        self, connection: sqlite3.Connection
    ) -> tuple[LedgerEvent, ...]:
        rows = connection.execute(
            "SELECT * FROM events ORDER BY sequence_id ASC"
        ).fetchall()
        events: list[LedgerEvent] = []
        previous_hash = ZERO_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            event = self._row_to_event(row)
            if event.sequence_id != expected_sequence:
                raise LedgerIntegrityError(
                    "shadow event sequence is not contiguous from one"
                )
            if event.previous_entry_sha256 != previous_hash:
                raise LedgerIntegrityError(
                    "shadow event previous hash does not link to head"
                )
            expected_hash = _entry_sha256(event)
            if event.entry_sha256 != expected_hash:
                raise LedgerIntegrityError("shadow event entry hash does not reproduce")
            previous_hash = event.entry_sha256
            events.append(event)
        verified = tuple(events)
        offset = 0
        while offset < len(verified):
            transaction_size = verified[offset].transaction_size
            transaction = verified[offset : offset + transaction_size]
            if len(transaction) != transaction_size:
                raise LedgerIntegrityError("stored shadow transaction is incomplete")
            transaction_sha256 = transaction[0].transaction_sha256
            if any(
                event.transaction_sha256 != transaction_sha256
                or event.transaction_size != transaction_size
                or event.transaction_position != position
                for position, event in enumerate(transaction, start=1)
            ):
                raise LedgerIntegrityError(
                    "stored shadow transaction boundary is inconsistent"
                )
            if _stored_transaction_sha256(transaction) != transaction_sha256:
                raise LedgerIntegrityError(
                    "stored shadow transaction digest does not reproduce"
                )
            offset += transaction_size
        return verified

    @staticmethod
    def _stable_row_matches(row: sqlite3.Row, prepared: _PreparedSpec) -> bool:
        spec = prepared.spec
        return all(
            (
                row["event_type"] == spec.event_type,
                row["target_date"] == spec.target_date,
                row["station"] == spec.station,
                row["issue_id"] == spec.issue_id,
                row["protocol_config_sha256"] == spec.protocol_config_sha256,
                row["code_sha256"] == spec.code_sha256,
                row["environment_sha256"] == spec.environment_sha256,
                row["input_manifest_sha256"] == spec.input_manifest_sha256,
                row["model_manifest_sha256"] == spec.model_manifest_sha256,
                row["state_before_sha256"] == spec.state_before_sha256,
                row["state_after_sha256"] == spec.state_after_sha256,
                row["payload_json"] == prepared.payload_json,
            )
        )

    @staticmethod
    def _new_event(
        prepared: _PreparedSpec,
        *,
        sequence_id: int,
        previous_hash: str,
        transaction_sha256: str,
        transaction_position: int,
        transaction_size: int,
    ) -> LedgerEvent:
        spec = prepared.spec
        payload = _decode_stored_payload(prepared.payload_json)
        unhashed = LedgerEvent(
            sequence_id=sequence_id,
            event_key=spec.event_key,
            event_type=spec.event_type,
            recorded_at_utc=_utc_now_text(),
            transaction_sha256=transaction_sha256,
            transaction_position=transaction_position,
            transaction_size=transaction_size,
            target_date=spec.target_date,
            station=spec.station,
            issue_id=spec.issue_id,
            protocol_config_sha256=spec.protocol_config_sha256,
            code_sha256=spec.code_sha256,
            environment_sha256=spec.environment_sha256,
            input_manifest_sha256=spec.input_manifest_sha256,
            model_manifest_sha256=spec.model_manifest_sha256,
            state_before_sha256=spec.state_before_sha256,
            state_after_sha256=spec.state_after_sha256,
            payload=payload,
            previous_entry_sha256=previous_hash,
            entry_sha256=ZERO_HASH,
        )
        return replace(unhashed, entry_sha256=_entry_sha256(unhashed))

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        event: LedgerEvent,
        payload_json: str,
    ) -> None:
        placeholders = ", ".join("?" for _ in _EVENT_COLUMNS)
        columns = ", ".join(_EVENT_COLUMNS)
        values = (
            event.sequence_id,
            event.event_key,
            event.event_type,
            event.recorded_at_utc,
            event.transaction_sha256,
            event.transaction_position,
            event.transaction_size,
            event.target_date,
            event.station,
            event.issue_id,
            event.protocol_config_sha256,
            event.code_sha256,
            event.environment_sha256,
            event.input_manifest_sha256,
            event.model_manifest_sha256,
            event.state_before_sha256,
            event.state_after_sha256,
            payload_json,
            event.previous_entry_sha256,
            event.entry_sha256,
        )
        connection.execute(
            f"INSERT INTO events ({columns}) VALUES ({placeholders})", values
        )

    def append_transaction(self, specs: Iterable[EventSpec]) -> tuple[LedgerEvent, ...]:
        """Atomically append a non-empty batch, or return its exact retry.

        A retry is accepted only when every key exists, every caller-controlled
        field matches canonical bytes, and the original rows are one contiguous
        batch in the same order.  Partial or changed retries fail closed.
        """

        try:
            prepared = tuple(_prepare_spec(spec) for spec in specs)
        except LedgerError:
            raise
        except (RecursionError, TypeError) as exc:
            raise LedgerValidationError(
                "append_transaction requires an iterable of EventSpec values"
            ) from exc
        if not prepared:
            raise LedgerValidationError("append_transaction requires events")
        keys = [item.spec.event_key for item in prepared]
        if len(keys) != len(set(keys)):
            raise LedgerValidationError("append batch contains duplicate event_key")
        transaction_sha256 = _transaction_sha256(prepared)
        transaction_size = len(prepared)

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_schema(connection)
            existing_chain = self._validate_chain_locked(connection)
            existing_rows: dict[str, sqlite3.Row] = {}
            for key in keys:
                row = connection.execute(
                    "SELECT * FROM events WHERE event_key = ?", (key,)
                ).fetchone()
                if row is not None:
                    existing_rows[key] = row

            if existing_rows:
                if len(existing_rows) != len(prepared):
                    raise LedgerConflictError(
                        "append retry is partial: only some event keys exist"
                    )
                returned: list[LedgerEvent] = []
                for position, item in enumerate(prepared, start=1):
                    row = existing_rows[item.spec.event_key]
                    if (
                        not self._stable_row_matches(row, item)
                        or row["transaction_sha256"] != transaction_sha256
                        or row["transaction_position"] != position
                        or row["transaction_size"] != transaction_size
                    ):
                        raise LedgerConflictError(
                            f"event_key content conflict: {item.spec.event_key}"
                        )
                    returned.append(self._row_to_event(row))
                sequences = [event.sequence_id for event in returned]
                expected_sequences = list(
                    range(sequences[0], sequences[0] + len(sequences))
                )
                if sequences != expected_sequences:
                    raise LedgerConflictError(
                        "append retry order is not one contiguous original batch"
                    )
                connection.commit()
                return tuple(returned)

            sequence_id = existing_chain[-1].sequence_id + 1 if existing_chain else 1
            previous_hash = (
                existing_chain[-1].entry_sha256 if existing_chain else ZERO_HASH
            )
            appended: list[LedgerEvent] = []
            for position, item in enumerate(prepared, start=1):
                event = self._new_event(
                    item,
                    sequence_id=sequence_id,
                    previous_hash=previous_hash,
                    transaction_sha256=transaction_sha256,
                    transaction_position=position,
                    transaction_size=transaction_size,
                )
                self._insert_event(connection, event, item.payload_json)
                appended.append(event)
                sequence_id += 1
                previous_hash = event.entry_sha256

            verified = self._validate_chain_locked(connection)
            if verified[-len(appended) :] != tuple(appended):
                raise LedgerIntegrityError(
                    "new shadow events failed in-transaction verification"
                )
            connection.commit()
            return tuple(appended)
        except LedgerError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise LedgerError("atomic shadow ledger append failed") from exc
        finally:
            connection.close()

    def _verified_events(self) -> tuple[LedgerEvent, ...]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            self._verify_schema(connection)
            events = self._validate_chain_locked(connection)
            connection.commit()
            return events
        except LedgerError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise LedgerError("shadow ledger read failed closed") from exc
        finally:
            connection.close()

    def validate_chain(self) -> LedgerEvent | None:
        """Validate the complete schema and chain and return its head."""

        events = self._verified_events()
        return events[-1] if events else None

    def read_events(
        self,
        *,
        after_sequence_id: int = 0,
        limit: int | None = None,
    ) -> tuple[LedgerEvent, ...]:
        """Return verified shadow events in sequence order."""

        if (
            not isinstance(after_sequence_id, int)
            or isinstance(after_sequence_id, bool)
            or after_sequence_id < 0
        ):
            raise LedgerValidationError(
                "after_sequence_id must be a non-negative integer"
            )
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
        ):
            raise LedgerValidationError("limit must be a positive integer")
        events = tuple(
            event
            for event in self._verified_events()
            if event.sequence_id > after_sequence_id
        )
        return events if limit is None else events[:limit]

    def head(self) -> LedgerEvent | None:
        """Return the verified chain head, or ``None`` for an empty ledger."""

        return self.validate_chain()

    def event_by_key(self, event_key: str) -> LedgerEvent | None:
        """Return one event by stable idempotency key after full verification."""

        _validate_text(event_key, "event_key")
        return next(
            (
                event
                for event in self._verified_events()
                if event.event_key == event_key
            ),
            None,
        )

    def close(self) -> None:
        """Compatibility no-op; operations intentionally use short connections."""

    def __enter__(self) -> AppendOnlyLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


AppendOnlyShadowLedger = AppendOnlyLedger
ShadowLedgerError = LedgerError
ShadowLedgerValidationError = LedgerValidationError
ShadowLedgerConflictError = LedgerConflictError
ShadowLedgerSchemaError = LedgerSchemaError
ShadowLedgerIntegrityError = LedgerIntegrityError


__all__ = [
    "ALLOWED_EVENT_TYPES",
    "APPLICATION_ID",
    "AppendOnlyLedger",
    "AppendOnlyShadowLedger",
    "EventSpec",
    "LedgerConflictError",
    "LedgerError",
    "LedgerEvent",
    "LedgerIntegrityError",
    "LedgerSchemaError",
    "LedgerValidationError",
    "SCHEMA_VERSION",
    "ShadowLedgerConflictError",
    "ShadowLedgerError",
    "ShadowLedgerIntegrityError",
    "ShadowLedgerSchemaError",
    "ShadowLedgerValidationError",
    "ZERO_HASH",
]
