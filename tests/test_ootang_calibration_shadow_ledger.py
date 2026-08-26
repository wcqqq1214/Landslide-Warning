"""Contracts for the independent Ootang calibration-shadow ledger."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_calibration_shadow_ledger as shadow  # noqa: E402
from monitoring import ootang_live_ledger as live  # noqa: E402


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


def _spec(
    event_key: str,
    *,
    payload: dict[str, object] | None = None,
) -> shadow.EventSpec:
    return shadow.EventSpec(
        event_key=event_key,
        event_type="shadow_candidate_issued",
        target_date="2030-01-02",
        station="ATU1",
        issue_id="shadow-issue-2030-01-02",
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
    """Temporarily remove append-only triggers to simulate offline damage."""

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


def _stored_values(event: shadow.LedgerEvent) -> tuple[object, ...]:
    return (
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
        json.dumps(
            event.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        event.previous_entry_sha256,
        event.entry_sha256,
    )


class CalibrationShadowLedgerTests(unittest.TestCase):
    def test_identity_and_event_allowlist_are_independent_and_exact(self) -> None:
        expected = {
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
        self.assertEqual(shadow.ALLOWED_EVENT_TYPES, expected)
        self.assertNotEqual(shadow.APPLICATION_ID, live.APPLICATION_ID)

        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "shadow.sqlite3"
            ledger = shadow.AppendOnlyLedger(path)
            for sequence, event_type in enumerate(sorted(expected), start=1):
                ledger.append_transaction(
                    [
                        replace(
                            _spec(f"event/{sequence}"),
                            event_type=event_type,
                            protocol_config_sha256=shadow.ZERO_HASH,
                            code_sha256=shadow.ZERO_HASH,
                            environment_sha256=shadow.ZERO_HASH,
                            input_manifest_sha256=shadow.ZERO_HASH,
                            model_manifest_sha256=shadow.ZERO_HASH,
                            state_before_sha256=shadow.ZERO_HASH,
                            state_after_sha256=shadow.ZERO_HASH,
                        )
                    ]
                )
            with self.assertRaises(shadow.LedgerValidationError):
                ledger.append_transaction(
                    [replace(_spec("event/unknown"), event_type="station_issue")]
                )

            connection = sqlite3.connect(path)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA application_id").fetchone()[0],
                    shadow.APPLICATION_ID,
                )
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    shadow.SCHEMA_VERSION,
                )
            finally:
                connection.close()

    def test_atomic_batch_chain_and_read_apis(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            ledger = shadow.AppendOnlyShadowLedger(Path(directory) / "shadow.sqlite3")
            events = ledger.append_transaction(
                [_spec("issue/ATU1"), replace(_spec("issue/ATU2"), station="ATU2")]
            )

            self.assertEqual([event.sequence_id for event in events], [1, 2])
            self.assertEqual(events[0].previous_entry_sha256, shadow.ZERO_HASH)
            self.assertEqual(events[1].previous_entry_sha256, events[0].entry_sha256)
            self.assertEqual([event.transaction_position for event in events], [1, 2])
            self.assertTrue(all(event.transaction_size == 2 for event in events))
            self.assertEqual(events[0].transaction_sha256, events[1].transaction_sha256)
            self.assertEqual(ledger.read_events(), events)
            self.assertEqual(ledger.read_events(after_sequence_id=1), events[1:])
            self.assertEqual(ledger.read_events(limit=1), events[:1])
            self.assertEqual(ledger.head(), events[-1])
            self.assertEqual(ledger.event_by_key("issue/ATU1"), events[0])
            self.assertIsNone(ledger.event_by_key("missing"))

    def test_identical_retry_is_idempotent_and_keeps_original_time(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "shadow.sqlite3"
            ledger = shadow.AppendOnlyLedger(path)
            specs = (
                _spec("issue/ATU1", payload={"z": 2, "a": 1}),
                replace(_spec("issue/ATU2"), station="ATU2"),
            )
            first = ledger.append_transaction(specs)
            retried = ledger.append_transaction(
                (
                    _spec("issue/ATU1", payload={"a": 1, "z": 2}),
                    replace(_spec("issue/ATU2"), station="ATU2"),
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

    def test_partial_changed_and_reordered_retry_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            ledger = shadow.AppendOnlyLedger(Path(directory) / "shadow.sqlite3")
            first = _spec("issue/ATU1")
            second = replace(_spec("issue/ATU2"), station="ATU2")
            original = ledger.append_transaction([first, second])

            with self.assertRaises(shadow.LedgerConflictError):
                ledger.append_transaction(
                    [replace(first, payload={"point_forecast_mm": 2.0}), second]
                )
            with self.assertRaises(shadow.LedgerConflictError):
                ledger.append_transaction([first])
            with self.assertRaises(shadow.LedgerConflictError):
                ledger.append_transaction([second])
            with self.assertRaises(shadow.LedgerConflictError):
                ledger.append_transaction([first, _spec("issue/ATU3")])
            with self.assertRaises(shadow.LedgerConflictError):
                ledger.append_transaction([second, first])

            self.assertEqual(ledger.read_events(), original)

    def test_nonfinite_or_noncanonical_input_rolls_back_entire_batch(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            ledger = shadow.AppendOnlyLedger(Path(directory) / "shadow.sqlite3")
            for value in (float("nan"), float("inf"), float("-inf")):
                with (
                    self.subTest(value=value),
                    self.assertRaises(shadow.LedgerValidationError),
                ):
                    ledger.append_transaction(
                        [
                            _spec("issue/ATU1"),
                            _spec("issue/ATU2", payload={"bad": value}),
                        ]
                    )
            with self.assertRaises(shadow.LedgerValidationError):
                ledger.append_transaction(
                    [replace(_spec("bad/hash"), code_sha256="BAD")]
                )
            with self.assertRaises(shadow.LedgerValidationError):
                ledger.append_transaction(
                    [replace(_spec("bad/date"), target_date="2030-1-2")]
                )
            with self.assertRaises(shadow.LedgerValidationError):
                ledger.append_transaction([_spec("duplicate"), _spec("duplicate")])

            self.assertEqual(ledger.read_events(), ())
            self.assertEqual(
                ledger.append_transaction([_spec("valid")])[0].sequence_id, 1
            )

    def test_deep_payload_errors_are_normalized_on_append_and_read(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "shadow.sqlite3"
            ledger = shadow.AppendOnlyLedger(path)
            nested: object = 0
            for _ in range(2000):
                nested = [nested]
            with self.assertRaises(shadow.LedgerValidationError):
                ledger.append_transaction(
                    [_spec("deep/append", payload={"deep": nested})]
                )
            self.assertEqual(ledger.read_events(), ())

            ledger.append_transaction([_spec("deep/read")])
            deep_json = '{"deep":' + "[" * 2000 + "0" + "]" * 2000 + "}"
            _tamper_rows(
                path,
                "UPDATE events SET payload_json = ? WHERE sequence_id = 1",
                (deep_json,),
            )
            with self.assertRaises(shadow.LedgerIntegrityError):
                ledger.read_events()

    def test_invalid_constructor_and_iterable_errors_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            invalid_timeouts: tuple[object, ...] = (
                None,
                "1",
                True,
                False,
                0,
                -1,
                float("nan"),
                float("inf"),
                float("-inf"),
            )
            for index, timeout in enumerate(invalid_timeouts):
                with (
                    self.subTest(timeout=timeout),
                    self.assertRaises(shadow.LedgerValidationError),
                ):
                    shadow.AppendOnlyLedger(
                        root / f"invalid-{index}.sqlite3",
                        timeout_seconds=timeout,  # type: ignore[arg-type]
                    )

            blocker = root / "not-a-directory"
            blocker.write_bytes(b"blocked")
            with self.assertRaises(shadow.LedgerSchemaError):
                shadow.AppendOnlyLedger(blocker / "child" / "shadow.sqlite3")

            ledger = shadow.AppendOnlyLedger(root / "valid.sqlite3")
            with self.assertRaises(shadow.LedgerValidationError):
                ledger.append_transaction(None)  # type: ignore[arg-type]
            self.assertEqual(ledger.read_events(), ())

    def test_wal_full_sync_and_append_only_triggers(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "shadow.sqlite3"
            ledger = shadow.AppendOnlyLedger(path)
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

    def test_external_replace_and_upsert_cannot_rewrite_history(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "shadow.sqlite3"
            ledger = shadow.AppendOnlyLedger(path)
            original = ledger.append_transaction([_spec("issue/ATU1")])[0]

            changed_spec = _spec("issue/ATU1", payload={"point_forecast_mm": 999.0})
            prepared = shadow._prepare_spec(changed_spec)  # noqa: SLF001
            transaction_sha256 = shadow._transaction_sha256(  # noqa: SLF001
                (prepared,)
            )
            unhashed = replace(
                original,
                transaction_sha256=transaction_sha256,
                payload=dict(changed_spec.payload),
                entry_sha256=shadow.ZERO_HASH,
            )
            replacement = replace(
                unhashed,
                entry_sha256=shadow._entry_sha256(unhashed),  # noqa: SLF001
            )
            columns = (
                "sequence_id,event_key,event_type,recorded_at_utc,"
                "transaction_sha256,transaction_position,transaction_size,"
                "target_date,station,issue_id,protocol_config_sha256,code_sha256,"
                "environment_sha256,input_manifest_sha256,model_manifest_sha256,"
                "state_before_sha256,state_after_sha256,payload_json,"
                "previous_entry_sha256,entry_sha256"
            )
            placeholders = ",".join("?" for _ in range(20))
            connection = sqlite3.connect(path)
            try:
                # This is SQLite's default on an unrelated external connection.
                connection.execute("PRAGMA recursive_triggers=OFF")
                self.assertEqual(
                    connection.execute("PRAGMA recursive_triggers").fetchone()[0],
                    0,
                )
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "conflicting insert or replace"
                ):
                    connection.execute(
                        f"INSERT OR REPLACE INTO events ({columns}) "
                        f"VALUES ({placeholders})",
                        _stored_values(replacement),
                    )
                connection.rollback()

                # UPSERT must be blocked independently of REPLACE's implicit delete.
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "conflicting insert or replace"
                ):
                    connection.execute(
                        f"INSERT INTO events ({columns}) VALUES ({placeholders}) "
                        "ON CONFLICT(event_key) DO UPDATE SET "
                        "payload_json=excluded.payload_json, "
                        "entry_sha256=excluded.entry_sha256",
                        _stored_values(replacement),
                    )
                connection.rollback()
            finally:
                connection.close()

            self.assertEqual(ledger.read_events(), (original,))
            self.assertEqual(ledger.head(), original)

    def test_payload_and_chain_tamper_are_detected_on_every_public_read(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            payload_path = Path(directory) / "payload.sqlite3"
            payload_ledger = shadow.AppendOnlyLedger(payload_path)
            payload_ledger.append_transaction([_spec("issue/ATU1")])
            _tamper_rows(
                payload_path,
                "UPDATE events SET payload_json = ? WHERE sequence_id = 1",
                (json.dumps({"point_forecast_mm": 9.0}, separators=(",", ":")),),
            )
            with self.assertRaises(shadow.LedgerIntegrityError):
                payload_ledger.read_events()

            chain_path = Path(directory) / "chain.sqlite3"
            chain = shadow.AppendOnlyLedger(chain_path)
            chain.append_transaction(
                [_spec("issue/ATU1"), replace(_spec("issue/ATU2"), station="ATU2")]
            )
            _tamper_rows(
                chain_path,
                "UPDATE events SET previous_entry_sha256 = ? WHERE sequence_id = 2",
                ("f" * 64,),
            )
            with self.assertRaises(shadow.LedgerIntegrityError):
                chain.head()

    def test_noncanonical_or_duplicate_stored_payload_is_detected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            for name, damaged_payload in (
                ("duplicate", '{"x":1,"x":2}'),
                ("noncanonical", '{"z":2, "a":1}'),
                ("nonfinite", '{"x":NaN}'),
            ):
                with self.subTest(name=name):
                    path = Path(directory) / f"{name}.sqlite3"
                    ledger = shadow.AppendOnlyLedger(path)
                    ledger.append_transaction([_spec("issue/ATU1")])
                    _tamper_rows(
                        path,
                        "UPDATE events SET payload_json = ? WHERE sequence_id = 1",
                        (damaged_payload,),
                    )
                    with self.assertRaises(shadow.LedgerIntegrityError):
                        ledger.event_by_key("issue/ATU1")

    def test_schema_drift_and_foreign_application_id_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            schema_path = Path(directory) / "schema.sqlite3"
            shadow.AppendOnlyLedger(schema_path)
            connection = sqlite3.connect(schema_path)
            try:
                connection.execute("ALTER TABLE events ADD COLUMN injected TEXT")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(shadow.LedgerSchemaError):
                shadow.AppendOnlyLedger(schema_path)

            foreign_path = Path(directory) / "foreign.sqlite3"
            live.AppendOnlyLedger(foreign_path)
            with self.assertRaises(shadow.LedgerSchemaError):
                shadow.AppendOnlyLedger(foreign_path)

    def test_foreign_delete_database_is_rejected_without_any_mutation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "foreign.sqlite3"
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0],
                    "delete",
                )
                connection.execute("PRAGMA application_id=305419896")
                connection.execute(
                    "CREATE TABLE foreign_records (id INTEGER PRIMARY KEY, value TEXT)"
                )
                connection.execute(
                    "INSERT INTO foreign_records (id, value) VALUES (1, 'preserve-me')"
                )
                connection.commit()
            finally:
                connection.close()
            bytes_before = path.read_bytes()

            with self.assertRaises(shadow.LedgerSchemaError):
                shadow.AppendOnlyLedger(path)

            self.assertEqual(path.read_bytes(), bytes_before)
            self.assertFalse(path.with_name(path.name + "-wal").exists())
            self.assertFalse(path.with_name(path.name + "-shm").exists())
            verification = sqlite3.connect(path)
            try:
                self.assertEqual(
                    verification.execute("PRAGMA journal_mode").fetchone()[0],
                    "delete",
                )
                self.assertEqual(
                    verification.execute(
                        "SELECT id, value FROM foreign_records"
                    ).fetchall(),
                    [(1, "preserve-me")],
                )
                self.assertEqual(
                    verification.execute("PRAGMA application_id").fetchone()[0],
                    305419896,
                )
            finally:
                verification.close()

    def test_reopen_verifies_history_and_resumes_from_head(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "shadow.sqlite3"
            old_head = shadow.AppendOnlyLedger(path).append_transaction(
                [_spec("issue/ATU1")]
            )[0]

            reopened = shadow.AppendOnlyLedger(path)
            self.assertEqual(reopened.validate_chain(), old_head)
            new_head = reopened.append_transaction(
                [replace(_spec("issue/ATU2"), station="ATU2")]
            )[0]

            self.assertEqual(new_head.sequence_id, 2)
            self.assertEqual(new_head.previous_entry_sha256, old_head.entry_sha256)


if __name__ == "__main__":
    unittest.main()
