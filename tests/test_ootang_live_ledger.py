"""Contracts for the E2-A append-only SQLite event ledger."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_live_ledger as live_ledger  # noqa: E402


HASHES = {
    name: hashlib.sha256(name.encode("utf-8")).hexdigest()
    for name in (
        "protocol",
        "code",
        "environment",
        "input",
        "model",
        "state-before",
        "state-after",
    )
}


def _spec(event_key: str, *, payload: dict[str, object] | None = None):
    return live_ledger.EventSpec(
        event_key=event_key,
        event_type="station_issue",
        target_date="2026-08-27",
        station="ATU1",
        issue_id="issue-2026-08-27",
        protocol_config_sha256=HASHES["protocol"],
        code_sha256=HASHES["code"],
        environment_sha256=HASHES["environment"],
        input_manifest_sha256=HASHES["input"],
        model_manifest_sha256=HASHES["model"],
        state_before_sha256=HASHES["state-before"],
        state_after_sha256=HASHES["state-after"],
        payload={"point_forecast_mm": 1.25} if payload is None else payload,
    )


def _tamper_rows(path: Path, sql: str, parameters: tuple[object, ...]) -> None:
    """Bypass append triggers while restoring their exact schema afterwards."""

    connection = sqlite3.connect(path)
    try:
        trigger_rows = connection.execute(
            "SELECT name, sql FROM sqlite_schema "
            "WHERE type = 'trigger' AND tbl_name = 'events' ORDER BY name"
        ).fetchall()
        connection.execute("BEGIN IMMEDIATE")
        for name, _trigger_sql in trigger_rows:
            connection.execute(f'DROP TRIGGER "{name}"')
        connection.execute(sql, parameters)
        for _name, trigger_sql in trigger_rows:
            connection.execute(trigger_sql)
        connection.commit()
    finally:
        connection.close()


class AppendOnlyLedgerTests(unittest.TestCase):
    def test_event_type_allowlist_is_exact_and_zero_hash_is_valid(self):
        expected = {
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
        self.assertEqual(live_ledger.ALLOWED_EVENT_TYPES, expected)

        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            ledger = live_ledger.AppendOnlyLedger(Path(directory) / "ledger.sqlite3")
            for sequence, event_type in enumerate(sorted(expected), start=1):
                zero_hash_spec = replace(
                    _spec(f"event/{sequence}"),
                    event_type=event_type,
                    protocol_config_sha256=live_ledger.ZERO_HASH,
                    code_sha256=live_ledger.ZERO_HASH,
                    environment_sha256=live_ledger.ZERO_HASH,
                    input_manifest_sha256=live_ledger.ZERO_HASH,
                    model_manifest_sha256=live_ledger.ZERO_HASH,
                    state_before_sha256=live_ledger.ZERO_HASH,
                    state_after_sha256=live_ledger.ZERO_HASH,
                )
                ledger.append_transaction([zero_hash_spec])
            with self.assertRaises(live_ledger.LedgerValidationError):
                ledger.append_transaction(
                    [replace(_spec("event/unknown"), event_type="unknown")]
                )

    def test_atomic_batch_is_read_back_as_one_contiguous_hash_chain(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            ledger = live_ledger.AppendOnlyLedger(Path(directory) / "ledger.sqlite3")
            events = ledger.append_transaction(
                [_spec("issue/ATU1"), _spec("issue/ATU2")]
            )

            self.assertEqual([event.sequence_id for event in events], [1, 2])
            self.assertEqual(events[0].previous_entry_sha256, live_ledger.ZERO_HASH)
            self.assertEqual(
                events[1].previous_entry_sha256, events[0].entry_sha256
            )
            self.assertEqual(ledger.read_events(), events)
            self.assertEqual(ledger.head(), events[-1])
            self.assertEqual(ledger.event_by_key("issue/ATU1"), events[0])

    def test_complete_identical_retry_is_idempotent_and_keeps_ledger_time(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "ledger.sqlite3"
            ledger = live_ledger.AppendOnlyLedger(path)
            specs = (
                _spec("issue/ATU1", payload={"z": 2, "a": 1}),
                _spec("issue/ATU2"),
            )
            first = ledger.append_transaction(specs)
            retried = ledger.append_transaction(
                (
                    _spec("issue/ATU1", payload={"a": 1, "z": 2}),
                    _spec("issue/ATU2"),
                )
            )

            self.assertEqual(retried, first)
            self.assertEqual(len(ledger.read_events()), 2)
            self.assertEqual(
                [event.recorded_at_utc for event in retried],
                [event.recorded_at_utc for event in first],
            )
            connection = sqlite3.connect(path)
            try:
                payload_json = connection.execute(
                    "SELECT payload_json FROM events WHERE event_key = ?",
                    ("issue/ATU1",),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(payload_json, '{"a":1,"z":2}')

    def test_changed_or_partial_retry_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            ledger = live_ledger.AppendOnlyLedger(Path(directory) / "ledger.sqlite3")
            original = _spec("issue/ATU1")
            ledger.append_transaction([original])

            with self.assertRaises(live_ledger.LedgerConflictError):
                ledger.append_transaction(
                    [replace(original, payload={"point_forecast_mm": 2.0})]
                )
            with self.assertRaises(live_ledger.LedgerConflictError):
                ledger.append_transaction([original, _spec("issue/ATU2")])

            self.assertEqual(
                [event.event_key for event in ledger.read_events()],
                ["issue/ATU1"],
            )

    def test_idempotent_retry_must_preserve_original_batch_order(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            ledger = live_ledger.AppendOnlyLedger(Path(directory) / "ledger.sqlite3")
            first = _spec("issue/ATU1")
            second = _spec("issue/ATU2")
            ledger.append_transaction([first, second])

            with self.assertRaises(live_ledger.LedgerConflictError):
                ledger.append_transaction([second, first])

    def test_invalid_batch_rolls_back_without_allocating_sequence(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            ledger = live_ledger.AppendOnlyLedger(Path(directory) / "ledger.sqlite3")

            with self.assertRaises(live_ledger.LedgerValidationError):
                ledger.append_transaction(
                    [
                        _spec("issue/ATU1"),
                        _spec("issue/ATU2", payload={"bad": float("nan")}),
                    ]
                )
            with self.assertRaises(live_ledger.LedgerValidationError):
                ledger.append_transaction(
                    [replace(_spec("issue/ATU3"), code_sha256="BAD")]
                )

            self.assertEqual(ledger.read_events(), ())
            event = ledger.append_transaction([_spec("issue/ATU1")])[0]
            self.assertEqual(event.sequence_id, 1)

    def test_database_uses_wal_full_sync_and_blocks_update_and_delete(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "ledger.sqlite3"
            ledger = live_ledger.AppendOnlyLedger(path)
            ledger.append_transaction([_spec("issue/ATU1")])

            connection = sqlite3.connect(path)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0], "wal"
                )
                self.assertEqual(
                    connection.execute("PRAGMA synchronous").fetchone()[0], 2
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute(
                        "UPDATE events SET station = 'ATU9' WHERE sequence_id = 1"
                    )
                connection.rollback()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute("DELETE FROM events WHERE sequence_id = 1")
            finally:
                connection.close()

            self.assertEqual(ledger.head().station, "ATU1")

    def test_payload_or_hash_chain_tampering_is_detected_on_reopen(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            payload_path = Path(directory) / "payload.sqlite3"
            ledger = live_ledger.AppendOnlyLedger(payload_path)
            ledger.append_transaction([_spec("issue/ATU1")])
            _tamper_rows(
                payload_path,
                "UPDATE events SET payload_json = ? WHERE sequence_id = 1",
                (json.dumps({"point_forecast_mm": 9.0}, separators=(",", ":")),),
            )
            with self.assertRaises(live_ledger.LedgerIntegrityError):
                live_ledger.AppendOnlyLedger(payload_path)

            chain_path = Path(directory) / "chain.sqlite3"
            chain = live_ledger.AppendOnlyLedger(chain_path)
            chain.append_transaction([_spec("issue/ATU1"), _spec("issue/ATU2")])
            _tamper_rows(
                chain_path,
                "UPDATE events SET previous_entry_sha256 = ? WHERE sequence_id = 2",
                ("f" * 64,),
            )
            with self.assertRaises(live_ledger.LedgerIntegrityError):
                live_ledger.AppendOnlyLedger(chain_path)

    def test_noncanonical_or_duplicate_payload_keys_are_detected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "ledger.sqlite3"
            ledger = live_ledger.AppendOnlyLedger(path)
            ledger.append_transaction([_spec("issue/ATU1")])
            _tamper_rows(
                path,
                "UPDATE events SET payload_json = ? WHERE sequence_id = 1",
                ('{"x":1,"x":2}',),
            )

            with self.assertRaises(live_ledger.LedgerIntegrityError):
                ledger.read_events()

    def test_unknown_schema_column_fails_strict_self_check(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "ledger.sqlite3"
            live_ledger.AppendOnlyLedger(path)
            connection = sqlite3.connect(path)
            try:
                connection.execute("ALTER TABLE events ADD COLUMN injected TEXT")
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(live_ledger.LedgerSchemaError):
                live_ledger.AppendOnlyLedger(path)

    def test_concurrent_same_batch_has_one_write_and_one_idempotent_retry(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "ledger.sqlite3"
            ledgers = [
                live_ledger.AppendOnlyLedger(path),
                live_ledger.AppendOnlyLedger(path),
            ]
            barrier = threading.Barrier(2)

            def append(index: int):
                barrier.wait()
                return ledgers[index].append_transaction(
                    [_spec("issue/ATU1"), _spec("issue/ATU2")]
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(append, range(2)))

            self.assertEqual(results[0], results[1])
            events = ledgers[0].read_events()
            self.assertEqual([event.sequence_id for event in events], [1, 2])

    def test_reopen_validates_history_and_resumes_from_verified_head(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "ledger.sqlite3"
            first = live_ledger.AppendOnlyLedger(path)
            old_head = first.append_transaction([_spec("issue/ATU1")])[0]

            reopened = live_ledger.AppendOnlyLedger(path)
            self.assertEqual(reopened.validate_chain(), old_head)
            new_head = reopened.append_transaction([_spec("issue/ATU2")])[0]

            self.assertEqual(new_head.sequence_id, 2)
            self.assertEqual(
                new_head.previous_entry_sha256, old_head.entry_sha256
            )


if __name__ == "__main__":
    unittest.main()
