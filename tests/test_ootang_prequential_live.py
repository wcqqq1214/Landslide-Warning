"""End-to-end contracts for the machine-polled Ootang E2-A live runner."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_live_ledger as ledger_module  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from monitoring.ootang_live_ledger import (  # noqa: E402
    AppendOnlyLedger,
    EventSpec,
)


WATERMARK = date(2030, 1, 1)
FIRST_TARGET = WATERMARK + timedelta(days=1)
ISSUE_CLOCK = datetime(2030, 1, 1, 15, 45, tzinfo=timezone.utc)
OUTCOME_CLOCK = datetime(2030, 1, 2, 8, 0, tzinfo=timezone.utc)
SECOND_ISSUE_CLOCK = datetime(2030, 1, 2, 15, 45, tzinfo=timezone.utc)
ANCHOR_TIME = "2030-01-01T15:50:00Z"
ANCHOR_URL = "https://time-anchor.invalid/v1/receipts"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


class _LiveFixture:
    """A complete disposable production/source/feed tree around a real ledger."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="ootang-live-test-", dir=ROOT
        )
        self.root = Path(self._temporary.name)
        self.profile = live.load_config(live.DEFAULT_CONFIG_PATH)
        self.paths = live.runtime_paths(self.profile, runtime_root=self.root)
        self.stations = list(self.profile["stations"])
        self.latest = {
            station: 100.0 + index
            for index, station in enumerate(self.stations)
        }
        self.source_manifest = self.root / "source_snapshot" / "manifest.json"
        self.model_manifest = self.root / "model_bundle" / "manifest.json"

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> _LiveFixture:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def _artifact(self, name: str, content: bytes | None = None) -> dict[str, object]:
        path = self.root / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if content is not None else name.encode("utf-8"))
        return {
            "path": str(path),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }

    def install_prerequisites(self, *, watermark: date = WATERMARK) -> None:
        source_payload: dict[str, object] = {
            "schema_version": self.profile["activation"][
                "source_snapshot_schema_version"
            ],
            "case": "ootang",
            "captured_at_utc": "2030-01-01T10:00:00Z",
            "maximum_complete_finalized_date": watermark.isoformat(),
            "stations": self.stations,
            "outcome_source_id": "ootang-survey-source-v1",
            "data_manifest": self._artifact("source-data-manifest.json", b"{}\n"),
            "latest_finalized_displacement_mm": self.latest,
        }
        self._write_json(self.source_manifest, source_payload)

        checkpoints = [
            {
                "seed": seed,
                "artifact": self._artifact(
                    f"checkpoint-seed-{seed}.bin", f"seed={seed}\n".encode()
                ),
            }
            for seed in range(5)
        ]
        target = self.profile["target"]
        model_payload: dict[str, object] = {
            "schema_version": self.profile["production_model"]["schema_version"],
            "case": "ootang",
            "model_version": "ootang-five-seed-production-test-v1",
            "created_at_utc": "2030-01-01T11:00:00Z",
            "training_cutoff_date": watermark.isoformat(),
            "stations": self.stations,
            "seeds": [0, 1, 2, 3, 4],
            "best_seed_selected": False,
            "target": {
                "name": target["name"],
                "unit": target["unit"],
                "horizon": target["horizon"],
            },
            "input_schema_sha256": "a" * 64,
            "training_manifest": self._artifact(
                "training-manifest.json", b"{}\n"
            ),
            "checkpoints": checkpoints,
        }
        self._write_json(self.model_manifest, model_payload)

    def issue_payload(
        self,
        target: date,
        *,
        persistence: dict[str, float] | None = None,
    ) -> dict[str, object]:
        target_start = datetime.combine(
            target,
            time.min,
            tzinfo=ZoneInfo(self.profile["target"]["date_timezone"]),
        ).astimezone(timezone.utc)
        persistence_values = persistence or self.latest
        expert_names = self.profile["algorithm_profile"]["expert_columns"]
        stations: list[dict[str, object]] = []
        for index, station in enumerate(self.stations):
            previous = persistence_values[station]
            row: dict[str, object] = {"station": station}
            for expert_index, expert in enumerate(expert_names):
                row[f"{expert}_mm"] = (
                    previous if expert_index == 0 else previous + 0.1 * expert_index
                )
            stations.append(row)
        input_manifest = self._artifact(
            f"issue-input-{target.isoformat()}.json", b"{}\n"
        )
        return {
            "schema_version": self.profile["issue_feed"]["schema_version"],
            "target_date": target.isoformat(),
            "generated_at_utc": _utc_text(target_start - timedelta(minutes=30)),
            "source_as_of_at_utc": _utc_text(target_start - timedelta(hours=1)),
            "source_snapshot_sha256": _sha256(self.source_manifest),
            "model_manifest_sha256": _sha256(self.model_manifest),
            "input_manifest": input_manifest,
            "stations": stations,
        }

    def write_issue(
        self,
        target: date = FIRST_TARGET,
        *,
        persistence: dict[str, float] | None = None,
        mutate: Callable[[dict[str, object]], None] | None = None,
    ) -> Path:
        payload = self.issue_payload(target, persistence=persistence)
        if mutate is not None:
            mutate(payload)
        path = self.root / "issue_inbox" / f"{target.isoformat()}.json"
        self._write_json(path, payload)
        return path

    def outcome_payload(
        self,
        target: date,
        *,
        revision_id: str = "revision-1",
        actual_offset: float = 1.0,
    ) -> dict[str, object]:
        target_start = datetime.combine(target, time.min, tzinfo=timezone.utc)
        source_manifest = self._artifact(
            f"outcome-source-{target.isoformat()}-{revision_id}.json",
            (revision_id + "\n").encode("utf-8"),
        )
        return {
            "schema_version": self.profile["outcome_feed"]["schema_version"],
            "target_date": target.isoformat(),
            "outcome_source_id": "ootang-survey-source-v1",
            "source_revision_id": revision_id,
            "source_observed_at_utc": _utc_text(
                target_start + timedelta(hours=2)
            ),
            "source_available_at_utc": _utc_text(
                target_start + timedelta(hours=3)
            ),
            "finalized_at_utc": _utc_text(target_start + timedelta(hours=4)),
            "finalized": True,
            "source_manifest": source_manifest,
            "stations": [
                {
                    "station": station,
                    "actual_mm": self.latest[station] + actual_offset,
                }
                for station in self.stations
            ],
        }

    def write_outcome(
        self,
        target: date = FIRST_TARGET,
        *,
        revision_id: str = "revision-1",
        actual_offset: float = 1.0,
        mutate: Callable[[dict[str, object]], None] | None = None,
    ) -> Path:
        payload = self.outcome_payload(
            target, revision_id=revision_id, actual_offset=actual_offset
        )
        if mutate is not None:
            mutate(payload)
        path = self.root / "outcome_inbox" / f"{target.isoformat()}.json"
        self._write_json(path, payload)
        return path

    def poll(
        self,
        *,
        now: datetime = ISSUE_CLOCK,
        anchor_client: live.AnchorClient | None = None,
    ) -> Path:
        return live.poll_live_runner(
            runtime_root=self.root,
            clock=lambda: now,
            anchor_client=anchor_client,
        )

    def status(self) -> dict[str, object]:
        return json.loads(self.paths.status.read_text(encoding="utf-8"))

    def events(self):
        return AppendOnlyLedger(self.paths.ledger).read_events()

    def anchor_client(
        self, anchored_at_utc: str = ANCHOR_TIME
    ) -> live.AnchorClient:
        def anchor(_endpoint: str, payload: dict[str, object]) -> dict[str, object]:
            return {
                "provider": "test-independent-anchor",
                "receipt_id": "receipt-1",
                "anchored_at_utc": anchored_at_utc,
                "root_sha256": payload["sealed_entry_sha256"],
                "receipt": {"test": True},
            }

        return anchor


class OotangPrequentialLiveTests(unittest.TestCase):
    """Exercise the full file-loader, lifecycle, ledger, and status boundary."""

    def test_missing_prerequisites_waits_without_creating_a_ledger_and_is_idempotent(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            first_path = fixture.poll()
            first = fixture.status()
            second_path = fixture.poll()
            second = fixture.status()

            self.assertEqual(first_path, fixture.paths.status)
            self.assertEqual(second_path, fixture.paths.status)
            self.assertEqual(
                first["runner_status"],
                "waiting_for_production_bundle_or_source_snapshot",
            )
            self.assertEqual(
                first["missing_prerequisites"],
                ["source_snapshot", "five_seed_production_model_bundle"],
            )
            self.assertEqual(first, second)
            self.assertFalse(fixture.paths.ledger.exists())

    def test_genesis_and_no_data_poll_are_idempotent(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            fixture.poll()
            first_status = fixture.status()
            first_events = fixture.events()
            fixture.poll()
            second_status = fixture.status()
            second_events = fixture.events()

            self.assertEqual(first_status["runner_status"], "waiting_for_new_data")
            self.assertEqual(first_status["reason"], "no_issue_or_outcome_for_next_natural_day")
            self.assertEqual(len(first_events), 1)
            self.assertEqual(first_events[0].event_type, "epoch_genesis")
            self.assertEqual(first_events, second_events)
            self.assertEqual(
                first_status["ledger_terminal_sha256"],
                second_status["ledger_terminal_sha256"],
            )
            station_states = first_events[0].payload["station_states"]
            self.assertTrue(
                all(
                    state["next_issue_reset_reason"] == "live_epoch_start"
                    for state in station_states.values()
                )
            )

    def test_contiguous_historical_outcomes_are_backfilled_without_online_update(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            fixture.write_outcome(FIRST_TARGET, actual_offset=1.0)
            fixture.write_outcome(
                FIRST_TARGET + timedelta(days=1), actual_offset=2.0
            )
            fixture.poll(
                now=datetime(2030, 1, 4, 1, 0, tzinfo=timezone.utc)
            )

            events = fixture.events()
            self.assertEqual(
                [event.event_type for event in events],
                ["epoch_genesis", "backfill_not_blind", "backfill_not_blind"],
            )
            genesis_hash = events[0].state_after_sha256
            for event in events[1:]:
                self.assertEqual(event.state_before_sha256, genesis_hash)
                self.assertEqual(event.state_after_sha256, genesis_hash)
                self.assertFalse(event.payload["online_state_updated"])
                self.assertFalse(event.payload["blind_metric_eligible"])
            status = fixture.status()
            self.assertEqual(status["runner_status"], "waiting_for_new_data")
            self.assertEqual(status["last_finalized_date"], "2030-01-03")
            self.assertEqual(status["backfill_not_blind_count"], 2)
            self.assertEqual(status["blind_settled_batch_count"], 0)

            before = events
            fixture.poll(
                now=datetime(2030, 1, 4, 1, 0, tzinfo=timezone.utc)
            )
            self.assertEqual(before, fixture.events())

    def test_future_issue_seal_anchor_then_outcome_is_loaded_in_that_order(self):
        with _LiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.write_issue()
            original_loader = live.load_outcome_batch
            loader_observations: list[list[str]] = []

            def guarded_loader(*args, **kwargs):
                event_types = [event.event_type for event in fixture.events()]
                loader_observations.append(event_types)
                self.assertIn("issue_batch_sealed", event_types)
                self.assertIn("anchor_confirmed", event_types)
                self.assertNotIn("outcome_batch_opened", event_types)
                return original_loader(*args, **kwargs)

            with mock.patch.dict(
                os.environ, {"OOTANG_TIME_ANCHOR_URL": ANCHOR_URL}, clear=False
            ):
                fixture.poll(anchor_client=fixture.anchor_client())
            self.assertEqual(fixture.status()["runner_status"], "waiting_for_outcome")
            self.assertEqual(loader_observations, [])

            fixture.write_outcome()
            with mock.patch.dict(
                os.environ, {"OOTANG_TIME_ANCHOR_URL": ANCHOR_URL}, clear=False
            ), mock.patch.object(
                live, "load_outcome_batch", side_effect=guarded_loader
            ):
                fixture.poll(
                    now=OUTCOME_CLOCK, anchor_client=fixture.anchor_client()
                )

            self.assertEqual(len(loader_observations), 1)
            events = fixture.events()
            event_types = [event.event_type for event in events]
            self.assertLess(
                event_types.index("issue_batch_sealed"),
                event_types.index("anchor_requested"),
            )
            self.assertLess(
                event_types.index("anchor_confirmed"),
                event_types.index("outcome_batch_opened"),
            )
            revealed_indices = [
                index
                for index, event_type in enumerate(event_types)
                if event_type == "outcome_revealed"
            ]
            score_indices = [
                index
                for index, event_type in enumerate(event_types)
                if event_type == "score_recorded"
            ]
            self.assertEqual(len(revealed_indices), 8)
            self.assertEqual(len(score_indices), 8)
            self.assertLess(max(revealed_indices), min(score_indices))
            settlement = next(
                event for event in events if event.event_type == "outcome_batch_settled"
            )
            self.assertTrue(settlement.payload["anchor_confirmed"])
            self.assertTrue(settlement.payload["externally_anchored_before_outcome"])
            self.assertTrue(
                settlement.payload["engineering_blind_time_order_candidate"]
            )
            self.assertFalse(settlement.payload["trusted_anchor_receipt_verified"])
            self.assertFalse(settlement.payload["e2_live_evidence_eligible"])
            status = fixture.status()
            self.assertEqual(status["engineering_blind_candidate_count"], 1)
            self.assertEqual(status["blind_settled_batch_count"], 0)
            self.assertFalse(status["e2_live_evidence_accumulated"])

    def test_outstanding_issue_is_idempotent_while_outcome_is_absent(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            fixture.write_issue()
            fixture.poll()
            first_events = fixture.events()
            fixture.poll()
            second_events = fixture.events()

            self.assertEqual(first_events, second_events)
            self.assertEqual(fixture.status()["runner_status"], "waiting_for_outcome")
            counts = {
                event_type: sum(
                    event.event_type == event_type for event in second_events
                )
                for event_type in (
                    "issue_batch_opened",
                    "station_issue",
                    "issue_batch_sealed",
                    "anchor_requested",
                    "anchor_failed",
                )
            }
            self.assertEqual(
                counts,
                {
                    "issue_batch_opened": 1,
                    "station_issue": 8,
                    "issue_batch_sealed": 1,
                    "anchor_requested": 1,
                    "anchor_failed": 1,
                },
            )

    def test_anchor_receipt_file_is_rebuilt_from_ledger_and_tamper_blocks(self):
        with _LiveFixture() as fixture, mock.patch.dict(
            os.environ, {"OOTANG_TIME_ANCHOR_URL": ANCHOR_URL}, clear=False
        ):
            fixture.install_prerequisites()
            fixture.write_issue()
            fixture.poll(anchor_client=fixture.anchor_client())
            events_before = fixture.events()
            seal = next(
                event for event in events_before if event.event_type == "issue_batch_sealed"
            )
            confirmation = next(
                event for event in events_before if event.event_type == "anchor_confirmed"
            )
            receipt_path = (
                fixture.paths.anchors
                / f"{seal.target_date}_{seal.entry_sha256}.json"
            )
            self.assertTrue(receipt_path.is_file())

            receipt_path.unlink()
            fixture.poll(anchor_client=fixture.anchor_client())
            self.assertTrue(receipt_path.is_file())
            self.assertEqual(events_before, fixture.events())
            rebuilt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(rebuilt["receipt_id"], confirmation.payload["receipt_id"])
            self.assertEqual(
                rebuilt["sealed_entry_sha256"], seal.entry_sha256
            )

            rebuilt["receipt_id"] = "tampered-receipt-id"
            fixture._write_json(receipt_path, rebuilt)
            with self.assertRaises(live.LiveIntegrityError):
                fixture.poll(anchor_client=fixture.anchor_client())
            self.assertEqual(events_before, fixture.events())
            self.assertEqual(fixture.status()["runner_status"], "blocked_integrity")

    def test_anchor_outside_seal_to_target_window_is_never_a_candidate(self):
        cases = (
            ("before_seal", "2000-01-01T00:00:00Z", False, True),
            ("after_target_start", "2030-01-01T16:01:00Z", True, False),
        )
        for name, anchor_time, follows_seal, precedes_target in cases:
            with self.subTest(name=name), _LiveFixture() as fixture, mock.patch.dict(
                os.environ, {"OOTANG_TIME_ANCHOR_URL": ANCHOR_URL}, clear=False
            ):
                fixture.install_prerequisites()
                fixture.write_issue()
                fixture.poll(anchor_client=fixture.anchor_client(anchor_time))
                fixture.write_outcome()
                fixture.poll(
                    now=OUTCOME_CLOCK,
                    anchor_client=fixture.anchor_client(anchor_time),
                )
                settlement = next(
                    event
                    for event in fixture.events()
                    if event.event_type == "outcome_batch_settled"
                )
                self.assertEqual(
                    settlement.payload["anchor_follows_durable_seal"],
                    follows_seal,
                )
                self.assertEqual(
                    settlement.payload["anchor_precedes_target_natural_day"],
                    precedes_target,
                )
                self.assertFalse(
                    settlement.payload["engineering_blind_time_order_candidate"]
                )
                self.assertFalse(
                    settlement.payload["trusted_anchor_receipt_verified"]
                )
                self.assertFalse(settlement.payload["e2_live_evidence_eligible"])
                self.assertEqual(
                    fixture.status()["engineering_blind_candidate_count"], 0
                )

    def test_revision_appends_retrospective_view_without_rewriting_state(self):
        with _LiveFixture() as fixture, mock.patch.dict(
            os.environ, {"OOTANG_TIME_ANCHOR_URL": ANCHOR_URL}, clear=False
        ):
            fixture.install_prerequisites()
            fixture.write_issue()
            fixture.poll(anchor_client=fixture.anchor_client())
            fixture.write_outcome()
            fixture.poll(now=OUTCOME_CLOCK, anchor_client=fixture.anchor_client())
            original_events = fixture.events()
            settlement = next(
                event
                for event in original_events
                if event.event_type == "outcome_batch_settled"
            )

            fixture.write_outcome(
                revision_id="revision-2", actual_offset=2.0
            )
            fixture.poll(now=OUTCOME_CLOCK, anchor_client=fixture.anchor_client())
            revised_events = fixture.events()
            self.assertEqual(revised_events[: len(original_events)], original_events)
            appended = revised_events[len(original_events) :]
            self.assertEqual(len(appended), 16)
            self.assertEqual(
                [event.event_type for event in appended].count("outcome_revision"),
                8,
            )
            self.assertEqual(
                [event.event_type for event in appended].count(
                    "revision_rescore_recorded"
                ),
                8,
            )
            self.assertTrue(
                all(event.state_before_sha256 == event.state_after_sha256 for event in appended)
            )
            current_settlement = next(
                event
                for event in revised_events
                if event.event_type == "outcome_batch_settled"
            )
            self.assertEqual(current_settlement, settlement)

            fixture.poll(now=OUTCOME_CLOCK, anchor_client=fixture.anchor_client())
            self.assertEqual(revised_events, fixture.events())

            fixture.write_outcome(
                revision_id="revision-2", actual_offset=3.0
            )
            with self.assertRaises(live.LiveIntegrityError):
                fixture.poll(now=OUTCOME_CLOCK, anchor_client=fixture.anchor_client())

    def test_latest_revision_updates_next_persistence_but_not_station_state(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            fixture.write_issue()
            fixture.poll()
            fixture.write_outcome()
            fixture.poll(now=OUTCOME_CLOCK)
            settled_events = fixture.events()
            effective_state_hashes = {
                event.station: event.state_after_sha256
                for event in settled_events
                if event.event_type == "drift_state_updated"
                and event.target_date == FIRST_TARGET.isoformat()
            }
            self.assertEqual(set(effective_state_hashes), set(fixture.stations))

            revised_values = {
                station: fixture.latest[station] + 2.0
                for station in fixture.stations
            }
            fixture.write_outcome(
                revision_id="revision-before-next-issue",
                actual_offset=2.0,
            )
            next_target = FIRST_TARGET + timedelta(days=1)
            fixture.write_issue(next_target, persistence=revised_values)
            fixture.poll(now=SECOND_ISSUE_CLOCK)

            events = fixture.events()
            revision_indices = [
                index
                for index, event in enumerate(events)
                if event.event_type == "outcome_revision"
                and event.payload.get("source_revision_id")
                == "revision-before-next-issue"
            ]
            next_issue_indices = [
                index
                for index, event in enumerate(events)
                if event.event_type == "station_issue"
                and event.target_date == next_target.isoformat()
            ]
            self.assertEqual(len(revision_indices), 8)
            self.assertEqual(len(next_issue_indices), 8)
            self.assertLess(max(revision_indices), min(next_issue_indices))
            next_issues = [events[index] for index in next_issue_indices]
            for event in next_issues:
                issue = event.payload["issue"]
                self.assertEqual(issue["experts_mm"][0], revised_values[event.station])
                self.assertEqual(issue["history_count"], 1)
                self.assertEqual(
                    issue["state_before_sha256"],
                    effective_state_hashes[event.station],
                )
            revision_events = [events[index] for index in revision_indices]
            self.assertTrue(
                all(
                    event.state_before_sha256 == event.state_after_sha256
                    and event.payload["live_online_state_rewritten"] is False
                    for event in revision_events
                )
            )

    def test_backfill_revision_is_append_only_and_fully_tracked(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            fixture.write_outcome()
            fixture.poll(now=OUTCOME_CLOCK)
            original_events = fixture.events()
            self.assertEqual(
                [event.event_type for event in original_events],
                ["epoch_genesis", "backfill_not_blind"],
            )

            fixture.write_outcome(
                revision_id="backfill-revision-2", actual_offset=2.0
            )
            fixture.poll(now=OUTCOME_CLOCK)
            revised_events = fixture.events()
            self.assertEqual(revised_events[: len(original_events)], original_events)
            appended = revised_events[len(original_events) :]
            self.assertEqual(len(appended), 8)
            self.assertTrue(
                all(event.event_type == "outcome_revision" for event in appended)
            )
            self.assertEqual(
                {event.station for event in appended}, set(fixture.stations)
            )
            self.assertTrue(
                all(
                    event.payload["original_classification"]
                    == "backfill_not_blind"
                    and event.payload["retrospective_score_available"] is False
                    and event.payload["live_online_state_rewritten"] is False
                    and event.state_before_sha256 == event.state_after_sha256
                    for event in appended
                )
            )
            before_retry = revised_events
            fixture.poll(now=OUTCOME_CLOCK)
            self.assertEqual(before_retry, fixture.events())

    def test_natural_day_gap_blocks_later_input_without_consuming_it(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            later = FIRST_TARGET + timedelta(days=1)
            fixture.write_issue(later)
            fixture.poll()

            status = fixture.status()
            self.assertEqual(status["runner_status"], "waiting_for_missing_natural_day")
            self.assertEqual(
                status["reason"], "later_input_exists_but_next_natural_day_is_missing"
            )
            self.assertEqual([event.event_type for event in fixture.events()], ["epoch_genesis"])

    def test_source_watermark_must_strictly_advance_beyond_historical_evidence(self):
        minimum = date(2020, 6, 30)
        for watermark in (minimum - timedelta(days=1), minimum):
            with self.subTest(watermark=watermark), _LiveFixture() as fixture, mock.patch.dict(
                os.environ, {}, clear=True
            ):
                fixture.install_prerequisites(watermark=watermark)
                with self.assertRaises(live.LiveInputError):
                    fixture.poll()
                self.assertFalse(fixture.paths.ledger.exists())
                status = fixture.status()
                self.assertEqual(status["runner_status"], "blocked_integrity")
                self.assertIn("must advance beyond", status["reason"])

    def test_issue_for_current_local_day_cannot_be_signed_retroactively(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            fixture.write_issue()
            fixture.poll(now=OUTCOME_CLOCK)

            status = fixture.status()
            self.assertEqual(status["runner_status"], "waiting_for_backfill_outcome")
            self.assertEqual(
                status["reason"], "historical_target_cannot_be_retroactively_issued"
            )
            self.assertEqual(
                [event.event_type for event in fixture.events()], ["epoch_genesis"]
            )

    def test_issue_pollution_and_incomplete_issue_station_batch_fail_before_append(self):
        mutations: tuple[tuple[str, Callable[[dict[str, object]], None]], ...] = (
            ("forbidden_actual", lambda payload: payload.update({"actual_mm": 1.0})),
            ("missing_station", lambda payload: payload["stations"].pop()),
        )
        for name, mutation in mutations:
            with self.subTest(name=name), _LiveFixture() as fixture, mock.patch.dict(
                os.environ, {}, clear=True
            ):
                fixture.install_prerequisites()
                fixture.write_issue(mutate=mutation)
                with self.assertRaises(live.LiveInputError):
                    fixture.poll()
                self.assertEqual(
                    [event.event_type for event in fixture.events()], ["epoch_genesis"]
                )
                self.assertEqual(fixture.status()["runner_status"], "blocked_integrity")

    def test_incomplete_outcome_and_nonmonotone_timestamps_fail_before_reveal(self):
        mutations: tuple[tuple[str, Callable[[dict[str, object]], None]], ...] = (
            ("missing_station", lambda payload: payload["stations"].pop()),
            (
                "nonmonotone_timestamps",
                lambda payload: payload.update(
                    {
                        "source_observed_at_utc": "2030-01-02T05:00:00Z",
                        "source_available_at_utc": "2030-01-02T04:00:00Z",
                    }
                ),
            ),
        )
        for name, mutation in mutations:
            with self.subTest(name=name), _LiveFixture() as fixture, mock.patch.dict(
                os.environ, {}, clear=True
            ):
                fixture.install_prerequisites()
                fixture.write_outcome(mutate=mutation)
                with self.assertRaises(live.LiveInputError):
                    fixture.poll(now=OUTCOME_CLOCK)
                self.assertEqual(
                    [event.event_type for event in fixture.events()], ["epoch_genesis"]
                )
                self.assertEqual(fixture.status()["runner_status"], "blocked_integrity")

    def test_source_or_model_manifest_hash_change_blocks_existing_epoch(self):
        for changed in ("source", "model"):
            with self.subTest(changed=changed), _LiveFixture() as fixture, mock.patch.dict(
                os.environ, {}, clear=True
            ):
                fixture.install_prerequisites()
                fixture.poll()
                original_events = fixture.events()
                path = (
                    fixture.source_manifest
                    if changed == "source"
                    else fixture.model_manifest
                )
                payload = json.loads(path.read_text(encoding="utf-8"))
                if changed == "source":
                    payload["captured_at_utc"] = "2030-01-01T10:01:00Z"
                else:
                    payload["model_version"] = "changed-model-version"
                fixture._write_json(path, payload)

                with self.assertRaises(live.LiveIntegrityError):
                    fixture.poll()
                self.assertEqual(original_events, fixture.events())
                self.assertEqual(fixture.status()["runner_status"], "blocked_integrity")

    def test_runner_lock_contention_fails_without_touching_runtime_state(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            fixture.paths.lock.parent.mkdir(parents=True, exist_ok=True)
            handle = fixture.paths.lock.open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with self.assertRaises(live.LiveRunnerBusy):
                    fixture.poll()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self.assertFalse(fixture.paths.ledger.exists())
            self.assertFalse(fixture.paths.status.exists())

    def test_sqlite_trigger_blocks_update_and_chain_tamper_fails_closed(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            fixture.poll()

            connection = sqlite3.connect(fixture.paths.ledger)
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE events SET payload_json = ? WHERE sequence_id = 1",
                        ('{"tampered":true}',),
                    )
                connection.rollback()
                connection.execute("DROP TRIGGER events_no_update")
                connection.execute(
                    "UPDATE events SET payload_json = ? WHERE sequence_id = 1",
                    ('{"tampered":true}',),
                )
                connection.execute(ledger_module._NO_UPDATE_TRIGGER_SQL)  # noqa: SLF001
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(live.LiveIntegrityError):
                fixture.poll()
            status = fixture.status()
            self.assertEqual(status["runner_status"], "blocked_integrity")
            self.assertTrue(status["ledger_validation_failed"])
            with self.assertRaises(ledger_module.LedgerIntegrityError):
                AppendOnlyLedger(fixture.paths.ledger)

    def test_chain_valid_genesis_with_false_watermark_is_rejected_by_replay(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            prerequisites = live.load_prerequisites(fixture.profile, fixture.paths)
            self.assertIsNotNone(prerequisites)
            assert prerequisites is not None
            reference = AppendOnlyLedger(fixture.root / "reference.sqlite3")
            valid = live._append_genesis(  # noqa: SLF001
                reference, fixture.profile, prerequisites
            )
            changed_payload = dict(valid.payload)
            changed_payload["activation_watermark"] = "2029-12-31"
            AppendOnlyLedger(fixture.paths.ledger).append_transaction(
                [
                    EventSpec(
                        event_key=valid.event_key,
                        event_type=valid.event_type,
                        target_date=valid.target_date,
                        station=valid.station,
                        issue_id=valid.issue_id,
                        protocol_config_sha256=valid.protocol_config_sha256,
                        code_sha256=valid.code_sha256,
                        environment_sha256=valid.environment_sha256,
                        input_manifest_sha256=valid.input_manifest_sha256,
                        model_manifest_sha256=valid.model_manifest_sha256,
                        state_before_sha256=valid.state_before_sha256,
                        state_after_sha256=valid.state_after_sha256,
                        payload=changed_payload,
                    )
                ]
            )

            with self.assertRaises(live.LiveIntegrityError):
                fixture.poll()
            self.assertEqual(fixture.status()["runner_status"], "blocked_integrity")

    def test_chain_valid_lifecycle_event_after_settlement_is_rejected(self):
        with _LiveFixture() as fixture, mock.patch.dict(os.environ, {}, clear=True):
            fixture.install_prerequisites()
            fixture.write_issue()
            fixture.poll()
            fixture.write_outcome()
            fixture.poll(now=OUTCOME_CLOCK)
            prerequisites = live.load_prerequisites(fixture.profile, fixture.paths)
            self.assertIsNotNone(prerequisites)
            assert prerequisites is not None
            settlement = next(
                event
                for event in fixture.events()
                if event.event_type == "outcome_batch_settled"
            )
            AppendOnlyLedger(fixture.paths.ledger).append_transaction(
                [
                    live._event_spec(  # noqa: SLF001
                        event_key="chain-valid-but-out-of-order-score",
                        event_type="score_recorded",
                        prerequisites=prerequisites,
                        payload={"bogus": True},
                        target_date_value=FIRST_TARGET,
                        station=fixture.stations[0],
                        issue_id="bogus-issue",
                        state_before_sha256=settlement.state_after_sha256,
                        state_after_sha256=settlement.state_after_sha256,
                    )
                ]
            )

            with self.assertRaises(live.LiveIntegrityError):
                fixture.poll(now=OUTCOME_CLOCK)
            self.assertEqual(fixture.status()["runner_status"], "blocked_integrity")


if __name__ == "__main__":
    unittest.main()
