"""Tamper-evident SQLite primitives for the Ootang E2 live ledger.

The ledger is deliberately small and dependency-free.  Every write uses one
``BEGIN IMMEDIATE`` transaction, and every public read verifies the complete
chain before returning data.  Entry hashes cover a UTF-8 JSON object encoded
with sorted keys, compact separators, and ``allow_nan=False``.  The entry hash
itself is the only envelope field excluded from that canonical object.

SHA-256 makes accidental or unobserved mutation detectable.  It does not
authenticate the writer or provide trusted time; those are later E2 concerns.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


ZERO_HASH = "0" * 64
SCHEMA_VERSION = 1
APPLICATION_ID = 0x4F4F544C  # ASCII "OOTL"

ALLOWED_EVENT_TYPES = frozenset(
    {
        "epoch_genesis",
        "backfill_not_blind",
        "issue_batch_opened",
        "station_issue",
        "issue_batch_sealed",
        "anchor_requested",
        "anchor_confirmed",
        "anchor_failed",
        "outcome_batch_opened",
        "outcome_revealed",
        "score_recorded",
        "expert_state_updated",
        "conformal_state_updated",
        "drift_state_updated",
        "fallback_or_abstain_recorded",
        "site_score_recorded",
        "outcome_batch_settled",
        "outcome_revision",
        "revision_rescore_recorded",
        "integrity_blocked",
        "epoch_closed",
    }
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_RECORDED_AT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)

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
        'epoch_genesis',
        'backfill_not_blind',
        'issue_batch_opened',
        'station_issue',
        'issue_batch_sealed',
        'anchor_requested',
        'anchor_confirmed',
        'anchor_failed',
        'outcome_batch_opened',
        'outcome_revealed',
        'score_recorded',
        'expert_state_updated',
        'conformal_state_updated',
        'drift_state_updated',
        'fallback_or_abstain_recorded',
        'site_score_recorded',
        'outcome_batch_settled',
        'outcome_revision',
        'revision_rescore_recorded',
        'integrity_blocked',
        'epoch_closed'
    )),
    recorded_at_utc TEXT NOT NULL,
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
_NO_UPDATE_TRIGGER_SQL = """CREATE TRIGGER events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events ledger is append-only');
END"""
_NO_DELETE_TRIGGER_SQL = """CREATE TRIGGER events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events ledger is append-only');
END"""

_SCHEMA_SQL = {
    ("table", "events"): _TABLE_SQL,
    ("index", "events_event_key_unique"): _EVENT_KEY_INDEX_SQL,
    ("index", "events_entry_sha256_unique"): _ENTRY_HASH_INDEX_SQL,
    ("trigger", "events_no_update"): _NO_UPDATE_TRIGGER_SQL,
    ("trigger", "events_no_delete"): _NO_DELETE_TRIGGER_SQL,
}


class LedgerError(RuntimeError):
    """Base error for fail-closed live-ledger operations."""


class LedgerValidationError(LedgerError):
    """Raised when a proposed event is not canonical or complete."""


class LedgerConflictError(LedgerError):
    """Raised when an event-key retry is partial or changes stable content."""


class LedgerSchemaError(LedgerError):
    """Raised when the database schema or append-only triggers drift."""


class LedgerIntegrityError(LedgerError):
    """Raised when materialized events do not reproduce the complete chain."""


@dataclass(frozen=True)
class EventSpec:
    """Stable caller-supplied content for one event.

    Sequence, recording time, previous hash, and entry hash are intentionally
    absent: the ledger assigns all four while holding the write transaction.
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
    """One fully assigned and chain-bound event returned by the ledger."""

    sequence_id: int
    event_key: str
    event_type: str
    recorded_at_utc: str
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
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
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
    except (json.JSONDecodeError, ValueError) as exc:
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


def _canonical_entry_json(event: LedgerEvent) -> str:
    envelope = {
        "sequence_id": event.sequence_id,
        "event_key": event.event_key,
        "event_type": event.event_type,
        "recorded_at_utc": event.recorded_at_utc,
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
    except (TypeError, ValueError) as exc:
        raise LedgerIntegrityError("event envelope is not canonical JSON") from exc


def _entry_sha256(event: LedgerEvent) -> str:
    return hashlib.sha256(_canonical_entry_json(event).encode("utf-8")).hexdigest()


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class AppendOnlyLedger:
    """A single-writer, idempotent, fully verified SQLite event ledger."""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.path = Path(path)
        if str(path) == ":memory:":
            raise LedgerValidationError("the ledger requires a durable file path")
        if timeout_seconds <= 0:
            raise LedgerValidationError("timeout_seconds must be positive")
        self.timeout_seconds = float(timeout_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self.timeout_seconds,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(
                f"PRAGMA busy_timeout={int(self.timeout_seconds * 1000)}"
            )
            mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
            if mode.lower() != "wal":
                mode = str(
                    connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                )
            if mode.lower() != "wal":
                connection.close()
                raise LedgerSchemaError("ledger database could not enter WAL mode")
            connection.execute("PRAGMA synchronous=FULL")
            synchronous = int(
                connection.execute("PRAGMA synchronous").fetchone()[0]
            )
            if synchronous != 2:
                connection.close()
                raise LedgerSchemaError("ledger database is not synchronous=FULL")
            return connection
        except LedgerError:
            raise
        except sqlite3.Error as exc:
            raise LedgerSchemaError("cannot open ledger database safely") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            objects = connection.execute(
                "SELECT type, name FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%'"
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
            raise LedgerSchemaError("ledger schema initialization failed") from exc
        finally:
            connection.close()

    def _verify_schema(self, connection: sqlite3.Connection) -> None:
        application_id = int(
            connection.execute("PRAGMA application_id").fetchone()[0]
        )
        user_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if application_id != APPLICATION_ID or user_version != SCHEMA_VERSION:
            raise LedgerSchemaError("ledger schema identity/version mismatch")

        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        actual_keys = {(row["type"], row["name"]) for row in rows}
        if actual_keys != set(_SCHEMA_SQL):
            raise LedgerSchemaError("ledger has missing or unknown schema objects")
        for row in rows:
            key = (row["type"], row["name"])
            if row["tbl_name"] != "events":
                raise LedgerSchemaError("ledger schema object targets unknown table")
            if not isinstance(row["sql"], str) or _normalized_sql(
                row["sql"]
            ) != _normalized_sql(_SCHEMA_SQL[key]):
                raise LedgerSchemaError(
                    f"ledger schema SQL drifted for {row['name']}"
                )

        columns = connection.execute("PRAGMA table_xinfo(events)").fetchall()
        names = [row["name"] for row in columns]
        if names != list(_EVENT_COLUMNS) or len(names) != len(set(names)):
            raise LedgerSchemaError("events table has missing, duplicate, or unknown columns")
        expected_required = {
            "sequence_id",
            "event_key",
            "event_type",
            "recorded_at_utc",
            *_HASH_COLUMNS,
            "payload_json",
            "previous_entry_sha256",
            "entry_sha256",
        }
        for row in columns:
            expected_type = "INTEGER" if row["name"] == "sequence_id" else "TEXT"
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
                    f"events column contract drifted for {row['name']}"
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
            raise LedgerSchemaError("events is not the expected STRICT table")

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> LedgerEvent:
        sequence_id = row["sequence_id"]
        if (
            not isinstance(sequence_id, int)
            or isinstance(sequence_id, bool)
            or sequence_id < 1
        ):
            raise LedgerIntegrityError("sequence_id is not a positive integer")
        _validate_text(
            row["event_key"], "event_key", error_type=LedgerIntegrityError
        )
        if row["event_type"] not in ALLOWED_EVENT_TYPES:
            raise LedgerIntegrityError("stored event_type is unknown")
        _validate_recorded_at(row["recorded_at_utc"])
        _validate_target_date(
            row["target_date"], error_type=LedgerIntegrityError
        )
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
                raise LedgerIntegrityError("event sequence is not contiguous from one")
            if event.previous_entry_sha256 != previous_hash:
                raise LedgerIntegrityError("event previous hash does not link to head")
            expected_hash = _entry_sha256(event)
            if event.entry_sha256 != expected_hash:
                raise LedgerIntegrityError("event entry hash does not reproduce")
            previous_hash = event.entry_sha256
            events.append(event)
        return tuple(events)

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
    ) -> LedgerEvent:
        spec = prepared.spec
        payload = _decode_stored_payload(prepared.payload_json)
        unhashed = LedgerEvent(
            sequence_id=sequence_id,
            event_key=spec.event_key,
            event_type=spec.event_type,
            recorded_at_utc=_utc_now_text(),
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

    def append_transaction(
        self, specs: Iterable[EventSpec]
    ) -> tuple[LedgerEvent, ...]:
        """Atomically append a non-empty batch, or return its exact retry.

        A retry is idempotent only when every incoming key already exists and
        every caller-controlled field is byte-for-byte stable after canonical
        payload serialization.  A partial retry or changed content is rejected.
        """

        prepared = tuple(_prepare_spec(spec) for spec in specs)
        if not prepared:
            raise LedgerValidationError("append_transaction requires events")
        keys = [item.spec.event_key for item in prepared]
        if len(keys) != len(set(keys)):
            raise LedgerValidationError("append batch contains duplicate event_key")

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
                for item in prepared:
                    row = existing_rows[item.spec.event_key]
                    if not self._stable_row_matches(row, item):
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
            for item in prepared:
                event = self._new_event(
                    item,
                    sequence_id=sequence_id,
                    previous_hash=previous_hash,
                )
                self._insert_event(connection, event, item.payload_json)
                appended.append(event)
                sequence_id += 1
                previous_hash = event.entry_sha256

            verified = self._validate_chain_locked(connection)
            if verified[-len(appended) :] != tuple(appended):
                raise LedgerIntegrityError(
                    "new events failed in-transaction materialized verification"
                )
            connection.commit()
            return tuple(appended)
        except LedgerError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise LedgerError("atomic ledger append failed") from exc
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
            raise LedgerError("ledger read failed closed") from exc
        finally:
            connection.close()

    def validate_chain(self) -> LedgerEvent | None:
        """Validate every sequence/hash/canonical-JSON link and return the head."""

        events = self._verified_events()
        return events[-1] if events else None

    def read_events(
        self,
        *,
        after_sequence_id: int = 0,
        limit: int | None = None,
    ) -> tuple[LedgerEvent, ...]:
        """Return verified events in sequence order."""

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
        """Return one verified event by its stable idempotency key."""

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
        """Compatibility no-op: operations intentionally use short connections."""

    def __enter__(self) -> AppendOnlyLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "ALLOWED_EVENT_TYPES",
    "AppendOnlyLedger",
    "EventSpec",
    "LedgerConflictError",
    "LedgerError",
    "LedgerEvent",
    "LedgerIntegrityError",
    "LedgerSchemaError",
    "LedgerValidationError",
    "ZERO_HASH",
]
