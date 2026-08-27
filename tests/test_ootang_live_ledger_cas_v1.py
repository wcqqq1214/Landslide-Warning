"""Fast contracts for recovery-only live-ledger expected-pre-head CAS v1."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_live_ledger as live  # noqa: E402
from monitoring import ootang_live_ledger_cas_v1 as cas  # noqa: E402


EPOCH_ID = "live-epoch-cas-test-v1"
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
    event_type: str = "anchor_requested",
    payload: dict[str, object] | None = None,
) -> live.EventSpec:
    return live.EventSpec(
        event_key=event_key,
        event_type=event_type,
        target_date="2026-08-27",
        station=None,
        issue_id=None,
        protocol_config_sha256=HASHES["protocol"],
        code_sha256=HASHES["code"],
        environment_sha256=HASHES["environment"],
        input_manifest_sha256=HASHES["input"],
        model_manifest_sha256=HASHES["model"],
        state_before_sha256=HASHES["state-before"],
        state_after_sha256=HASHES["state-after"],
        payload={"attempt": 1, "request_id": event_key} if payload is None else payload,
    )


def _genesis_spec() -> live.EventSpec:
    return replace(
        _spec(
            f"{EPOCH_ID}:epoch_genesis",
            event_type="epoch_genesis",
            payload={"live_epoch_id": EPOCH_ID},
        ),
        target_date=None,
        state_before_sha256=live.ZERO_HASH,
    )


def _pre_head(event: live.LedgerEvent) -> cas.LiveLedgerPreHeadV1:
    return cas.LiveLedgerPreHeadV1(
        epoch_id=EPOCH_ID,
        event_count=event.sequence_id,
        sequence_id=event.sequence_id,
        entry_sha256=event.entry_sha256,
    )


def _stored_row(path: Path, event_key: str) -> tuple[object, ...]:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT * FROM events WHERE event_key = ?", (event_key,)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AssertionError(f"missing stored event: {event_key}")
    return tuple(row)


class LiveLedgerCasV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.path = Path(self.temporary_directory.name) / "ledger.sqlite3"
        self.ledger = live.AppendOnlyLedger(self.path)
        self.genesis = self.ledger.append_transaction([_genesis_spec()])[0]
        self.expected = _pre_head(self.genesis)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_exact_pre_head_commits_once(self) -> None:
        spec = _spec("anchor/request/1")

        result = cas.append_transaction_at_pre_head_v1(
            self.ledger,
            expected_pre_head=self.expected,
            specs=[spec],
        )

        self.assertTrue(result.created)
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual(event.sequence_id, 2)
        self.assertEqual(event.previous_entry_sha256, self.genesis.entry_sha256)
        self.assertEqual(self.ledger.read_events(), (self.genesis, event))

    def test_stale_head_with_absent_key_rolls_back_without_writes(self) -> None:
        self.ledger.append_transaction([_spec("independent/suffix")])
        before = self.ledger.read_events()

        with self.assertRaises(cas.LiveLedgerCasConflictErrorV1):
            cas.append_transaction_at_pre_head_v1(
                self.ledger,
                expected_pre_head=self.expected,
                specs=[_spec("anchor/request/stale")],
            )

        self.assertEqual(self.ledger.read_events(), before)
        self.assertNotIn(
            "anchor/request/stale",
            [event.event_key for event in self.ledger.read_events()],
        )

    def test_exact_retry_adopts_original_stored_row(self) -> None:
        event_key = "anchor/request/retry"
        first_spec = _spec(
            event_key,
            payload={"attempt": 1, "request_id": event_key},
        )
        first = cas.append_transaction_at_pre_head_v1(
            self.ledger,
            expected_pre_head=self.expected,
            specs=[first_spec],
        )
        stored_before = _stored_row(self.path, event_key)

        retried = cas.append_transaction_at_pre_head_v1(
            self.ledger,
            expected_pre_head=self.expected,
            specs=[
                _spec(
                    event_key,
                    payload={"request_id": event_key, "attempt": 1},
                )
            ],
        )

        self.assertFalse(retried.created)
        self.assertEqual(retried.events, first.events)
        self.assertEqual(_stored_row(self.path, event_key), stored_before)
        self.assertEqual(len(self.ledger.read_events()), 2)

    def test_exact_retry_is_adopted_with_a_later_valid_suffix(self) -> None:
        spec = _spec("anchor/request/adopt")
        first = cas.append_transaction_at_pre_head_v1(
            self.ledger,
            expected_pre_head=self.expected,
            specs=[spec],
        )
        suffix = self.ledger.append_transaction([_spec("independent/suffix")])[0]

        adopted = cas.append_transaction_at_pre_head_v1(
            self.ledger,
            expected_pre_head=self.expected,
            specs=[spec],
        )

        self.assertFalse(adopted.created)
        self.assertEqual(adopted.events, first.events)
        self.assertEqual(
            self.ledger.read_events(),
            (self.genesis, first.events[0], suffix),
        )

    def test_partial_and_changed_retries_fail_closed(self) -> None:
        first = _spec("anchor/request/batch-1")
        second = _spec("anchor/request/batch-2")
        self.ledger.append_transaction([first])
        partial_before = self.ledger.read_events()

        with self.assertRaises(cas.LiveLedgerCasConflictErrorV1):
            cas.append_transaction_at_pre_head_v1(
                self.ledger,
                expected_pre_head=self.expected,
                specs=[first, second],
            )
        self.assertEqual(self.ledger.read_events(), partial_before)

        changed_path = Path(self.temporary_directory.name) / "changed.sqlite3"
        changed_ledger = live.AppendOnlyLedger(changed_path)
        changed_genesis = changed_ledger.append_transaction([_genesis_spec()])[0]
        changed_expected = _pre_head(changed_genesis)
        cas.append_transaction_at_pre_head_v1(
            changed_ledger,
            expected_pre_head=changed_expected,
            specs=[first, second],
        )
        changed_before = changed_ledger.read_events()

        with self.assertRaises(cas.LiveLedgerCasConflictErrorV1):
            cas.append_transaction_at_pre_head_v1(
                changed_ledger,
                expected_pre_head=changed_expected,
                specs=[
                    replace(first, payload={"attempt": 2}),
                    second,
                ],
            )
        self.assertEqual(changed_ledger.read_events(), changed_before)

    def test_wrong_epoch_or_position_is_rejected(self) -> None:
        wrong_values = (
            replace(self.expected, epoch_id="different-live-epoch"),
            replace(self.expected, entry_sha256="f" * 64),
            replace(self.expected, event_count=2, sequence_id=2),
        )

        for index, expected in enumerate(wrong_values):
            with self.subTest(index=index):
                with self.assertRaises(cas.LiveLedgerCasConflictErrorV1):
                    cas.append_transaction_at_pre_head_v1(
                        self.ledger,
                        expected_pre_head=expected,
                        specs=[_spec(f"anchor/request/wrong/{index}")],
                    )

        self.assertEqual(self.ledger.read_events(), (self.genesis,))


if __name__ == "__main__":
    unittest.main()
