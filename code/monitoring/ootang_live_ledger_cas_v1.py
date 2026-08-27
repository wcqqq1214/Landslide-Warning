"""Versioned expected-pre-head CAS for the frozen Ootang live ledger.

This module is deliberately additive.  The original live writer and ledger
implementation remain byte-frozen; recovery-only callers may use this helper
to bind an append to an exact, already observed ledger position while holding
the same SQLite ``BEGIN IMMEDIATE`` transaction that performs the write.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import sqlite3

from monitoring import ootang_live_ledger as live


class LiveLedgerCasErrorV1(live.LedgerError):
    """Base error for expected-pre-head CAS transaction failures."""


class LiveLedgerCasBusyErrorV1(LiveLedgerCasErrorV1):
    """Raised when SQLite cannot acquire the CAS write lock yet."""


class LiveLedgerCasValidationErrorV1(live.LedgerValidationError):
    """Raised when the expected pre-head contract is malformed."""


class LiveLedgerCasConflictErrorV1(live.LedgerConflictError):
    """Raised when the ledger no longer matches the expected pre-head."""


@dataclass(frozen=True)
class LiveLedgerPreHeadV1:
    """Exact non-empty live-ledger position observed by a recovery intent."""

    epoch_id: str
    event_count: int
    sequence_id: int
    entry_sha256: str


@dataclass(frozen=True)
class LiveLedgerCasAppendResultV1:
    """Events committed now or adopted from an exact crash-forward retry."""

    events: tuple[live.LedgerEvent, ...]
    created: bool


def _is_sqlite_busy(exc: BaseException) -> bool:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        error_code = getattr(current, "sqlite_errorcode", None)
        if isinstance(error_code, int) and (error_code & 0xFF) in {
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        }:
            return True
        if "locked" in str(current).lower() or "busy" in str(current).lower():
            return True
        current = current.__cause__ or current.__context__
    return False


def _validate_pre_head(expected: LiveLedgerPreHeadV1) -> None:
    if not isinstance(expected, LiveLedgerPreHeadV1):
        raise LiveLedgerCasValidationErrorV1(
            "expected_pre_head must be LiveLedgerPreHeadV1"
        )
    live._validate_text(  # noqa: SLF001
        expected.epoch_id,
        "epoch_id",
        error_type=LiveLedgerCasValidationErrorV1,
    )
    for name in ("event_count", "sequence_id"):
        value = getattr(expected, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise LiveLedgerCasValidationErrorV1(f"{name} must be a positive integer")
    if expected.event_count != expected.sequence_id:
        raise LiveLedgerCasValidationErrorV1(
            "event_count must equal the contiguous head sequence_id"
        )
    live._validate_hash(  # noqa: SLF001
        expected.entry_sha256,
        "entry_sha256",
        error_type=LiveLedgerCasValidationErrorV1,
    )


def _verify_expected_pre_head(
    chain: tuple[live.LedgerEvent, ...],
    expected: LiveLedgerPreHeadV1,
) -> None:
    if not chain:
        raise LiveLedgerCasConflictErrorV1(
            "expected-pre-head CAS requires a live epoch genesis"
        )
    genesis = chain[0]
    if (
        genesis.event_type != "epoch_genesis"
        or genesis.payload.get("live_epoch_id") != expected.epoch_id
    ):
        raise LiveLedgerCasConflictErrorV1(
            "live ledger genesis does not match the expected epoch"
        )
    if len(chain) < expected.event_count:
        raise LiveLedgerCasConflictErrorV1(
            "live ledger is shorter than the expected pre-head"
        )
    observed = chain[expected.event_count - 1]
    if (
        observed.sequence_id != expected.sequence_id
        or observed.entry_sha256 != expected.entry_sha256
    ):
        raise LiveLedgerCasConflictErrorV1(
            "live ledger does not contain the expected pre-head at its position"
        )


def append_transaction_at_pre_head_v1(
    ledger: live.AppendOnlyLedger,
    *,
    expected_pre_head: LiveLedgerPreHeadV1,
    specs: Iterable[live.EventSpec],
) -> LiveLedgerCasAppendResultV1:
    """Append only at ``expected_pre_head`` or adopt its exact stored retry.

    A fresh append requires the current terminal head to equal the expected
    pre-head.  If every event key already exists, a crash-forward retry is
    adopted only when the stored events form the exact contiguous chain slice
    immediately after that pre-head.  A later valid suffix is allowed only in
    this adoption branch.
    """

    if not isinstance(ledger, live.AppendOnlyLedger):
        raise LiveLedgerCasValidationErrorV1("ledger must be an AppendOnlyLedger")
    _validate_pre_head(expected_pre_head)
    prepared = tuple(live._prepare_spec(spec) for spec in specs)  # noqa: SLF001
    if not prepared:
        raise LiveLedgerCasValidationErrorV1("CAS append requires events")
    keys = [item.spec.event_key for item in prepared]
    if len(keys) != len(set(keys)):
        raise LiveLedgerCasValidationErrorV1(
            "CAS append batch contains duplicate event_key"
        )

    try:
        connection = ledger._connect()  # noqa: SLF001
    except live.LedgerError as exc:
        if _is_sqlite_busy(exc):
            raise LiveLedgerCasBusyErrorV1(
                "expected-pre-head CAS write lock is busy"
            ) from exc
        raise
    try:
        connection.execute("BEGIN IMMEDIATE")
        ledger._verify_schema(connection)  # noqa: SLF001
        existing_chain = ledger._validate_chain_locked(connection)  # noqa: SLF001
        _verify_expected_pre_head(existing_chain, expected_pre_head)

        existing_rows: dict[str, sqlite3.Row] = {}
        for key in keys:
            row = connection.execute(
                "SELECT * FROM events WHERE event_key = ?", (key,)
            ).fetchone()
            if row is not None:
                existing_rows[key] = row

        if existing_rows:
            if len(existing_rows) != len(prepared):
                raise LiveLedgerCasConflictErrorV1(
                    "CAS retry is partial: only some event keys exist"
                )
            returned: list[live.LedgerEvent] = []
            for item in prepared:
                row = existing_rows[item.spec.event_key]
                if not ledger._stable_row_matches(row, item):  # noqa: SLF001
                    raise LiveLedgerCasConflictErrorV1(
                        f"event_key content conflict: {item.spec.event_key}"
                    )
                returned.append(ledger._row_to_event(row))  # noqa: SLF001

            expected_sequences = list(
                range(
                    expected_pre_head.sequence_id + 1,
                    expected_pre_head.sequence_id + len(returned) + 1,
                )
            )
            if [event.sequence_id for event in returned] != expected_sequences:
                raise LiveLedgerCasConflictErrorV1(
                    "CAS retry is not the exact contiguous post-head slice"
                )
            if returned[0].previous_entry_sha256 != expected_pre_head.entry_sha256:
                raise LiveLedgerCasConflictErrorV1(
                    "CAS retry does not link directly to the expected pre-head"
                )
            start = expected_pre_head.event_count
            stop = start + len(returned)
            if tuple(existing_chain[start:stop]) != tuple(returned):
                raise LiveLedgerCasConflictErrorV1(
                    "CAS retry order or chain position does not match"
                )
            connection.commit()
            return LiveLedgerCasAppendResultV1(
                events=tuple(returned),
                created=False,
            )

        if len(existing_chain) != expected_pre_head.event_count:
            raise LiveLedgerCasConflictErrorV1(
                "live ledger terminal head moved after the recovery observation"
            )

        sequence_id = expected_pre_head.sequence_id + 1
        previous_hash = expected_pre_head.entry_sha256
        appended: list[live.LedgerEvent] = []
        for item in prepared:
            event = ledger._new_event(  # noqa: SLF001
                item,
                sequence_id=sequence_id,
                previous_hash=previous_hash,
            )
            ledger._insert_event(connection, event, item.payload_json)  # noqa: SLF001
            appended.append(event)
            sequence_id += 1
            previous_hash = event.entry_sha256

        verified = ledger._validate_chain_locked(connection)  # noqa: SLF001
        if verified != (*existing_chain, *appended):
            raise live.LedgerIntegrityError(
                "CAS events failed in-transaction materialized verification"
            )
        connection.commit()
        return LiveLedgerCasAppendResultV1(events=tuple(appended), created=True)
    except live.LedgerError:
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        if _is_sqlite_busy(exc):
            raise LiveLedgerCasBusyErrorV1(
                "expected-pre-head CAS write lock is busy"
            ) from exc
        raise LiveLedgerCasErrorV1("atomic expected-pre-head CAS failed") from exc
    finally:
        connection.close()


__all__ = [
    "LiveLedgerCasAppendResultV1",
    "LiveLedgerCasBusyErrorV1",
    "LiveLedgerCasConflictErrorV1",
    "LiveLedgerCasErrorV1",
    "LiveLedgerCasValidationErrorV1",
    "LiveLedgerPreHeadV1",
    "append_transaction_at_pre_head_v1",
]
