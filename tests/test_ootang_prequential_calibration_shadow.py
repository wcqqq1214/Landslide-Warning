"""End-to-end contracts for the independent calibration-shadow runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
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


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_calibration_shadow_ledger as ledger_module  # noqa: E402
from monitoring import ootang_prequential_calibration_shadow as shadow  # noqa: E402


STATIONS = shadow.STATIONS
FIXED_CLOCK = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _LiveEvent:
    sequence_id: int
    event_type: str
    payload: dict[str, object]
    target_date: str | None
    station: str | None
    entry_sha256: str
    transaction_sha256: str
    input_manifest_sha256: str
    model_manifest_sha256: str


class _LiveBuilder:
    def __init__(self, *, epoch_id: str = "verified-live-epoch-v1") -> None:
        self.epoch_id = epoch_id
        self.events: list[_LiveEvent] = []
        self.outstanding_target: str | None = None
        self.issue_events: dict[str, _LiveEvent] = {}
        self.seal_event: _LiveEvent | None = None
        self._append("epoch_genesis", {})

    def _append(
        self,
        event_type: str,
        payload: dict[str, object],
        *,
        target: str | None = None,
        station: str | None = None,
        transaction: str | None = None,
        input_token: str | None = None,
    ) -> _LiveEvent:
        sequence = len(self.events) + 1
        scientific = json.dumps(
            {
                "sequence": sequence,
                "event_type": event_type,
                "payload": payload,
                "target": target,
                "station": station,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        event = _LiveEvent(
            sequence_id=sequence,
            event_type=event_type,
            payload=payload,
            target_date=target,
            station=station,
            entry_sha256=_hash(scientific),
            transaction_sha256=_hash(transaction or f"transaction:{sequence}"),
            input_manifest_sha256=_hash(input_token or f"input:{sequence}"),
            model_manifest_sha256=_hash("fixed-model"),
        )
        self.events.append(event)
        return event

    def add_issue(
        self, target: str, *, point_offset: float = 0.0
    ) -> tuple[dict[str, _LiveEvent], _LiveEvent]:
        if self.outstanding_target is not None:
            raise AssertionError("test live builder already has an outstanding issue")
        transaction = f"issue:{target}"
        input_token = f"issue-input:{target}"
        issue_events: dict[str, _LiveEvent] = {}
        for index, station in enumerate(STATIONS):
            issue_events[station] = self._append(
                "station_issue",
                {
                    "issue": {
                        "point_forecast_mm": 100.0 + point_offset + index,
                    }
                },
                target=target,
                station=station,
                transaction=transaction,
                input_token=input_token,
            )
        seal = self._append(
            "issue_batch_sealed",
            {"issue_batch_sha256": _hash(f"live-issue-batch:{target}")},
            target=target,
            transaction=transaction,
            input_token=input_token,
        )
        self.outstanding_target = target
        self.issue_events = issue_events
        self.seal_event = seal
        return issue_events, seal

    def add_settlement(
        self,
        target: str,
        *,
        actual_offset: float = 0.5,
        revision_id: str = "original-v1",
        seal_sha256: str | None = None,
        drift_stations: tuple[str, ...] = (),
    ) -> _LiveEvent:
        transaction = f"outcome:{target}:{revision_id}"
        input_token = f"outcome-input:{target}:{revision_id}"
        seal = seal_sha256
        if seal is None:
            if self.seal_event is None:
                seal = _hash(f"missed-live-seal:{target}")
            else:
                seal = self.seal_event.entry_sha256
        for station in STATIONS:
            self._append(
                "drift_state_updated",
                {"drift_detected": station in drift_stations},
                target=target,
                station=station,
                transaction=transaction,
                input_token=input_token,
            )
        actuals = {
            station: 100.0 + actual_offset + index
            for index, station in enumerate(STATIONS)
        }
        settlement = self._append(
            "outcome_batch_settled",
            {
                "actual_by_station": actuals,
                "issue_batch_sealed_entry_sha256": seal,
                "outcome_batch_sha256": _hash(
                    f"outcome-batch:{target}:{revision_id}:{actual_offset}"
                ),
                "source_revision_id": revision_id,
            },
            target=target,
            transaction=transaction,
            input_token=input_token,
        )
        if self.outstanding_target == target:
            self.outstanding_target = None
            self.issue_events = {}
            self.seal_event = None
        return settlement

    def add_backfill(
        self, target: str, *, revision_id: str = "backfill-v1"
    ) -> _LiveEvent:
        return self._append(
            "backfill_not_blind",
            {
                "outcome_batch_sha256": _hash(f"backfill-batch:{target}:{revision_id}"),
                "source_revision_id": revision_id,
            },
            target=target,
            transaction=f"backfill:{target}:{revision_id}",
            input_token=f"backfill-input:{target}:{revision_id}",
        )

    def add_revision(
        self,
        target: str,
        revision_id: str,
        *,
        actual_offset: float,
    ) -> dict[str, _LiveEvent]:
        transaction = f"revision:{target}:{revision_id}"
        input_token = f"revision-input:{target}:{revision_id}"
        batch_sha = _hash(f"revision-batch:{target}:{revision_id}:{actual_offset}")
        rows: dict[str, _LiveEvent] = {}
        for index, station in enumerate(STATIONS):
            rows[station] = self._append(
                "outcome_revision",
                {
                    "source_revision_id": revision_id,
                    "outcome_batch_sha256": batch_sha,
                    "revised_actual_mm": 100.0 + actual_offset + index,
                },
                target=target,
                station=station,
                transaction=transaction,
                input_token=input_token,
            )
        return rows

    def add_anchor_failure(self, target: str) -> _LiveEvent:
        return self._append(
            "anchor_failed",
            {"reason": "test-only-provider-timeout"},
            target=target,
            transaction=f"anchor:{target}",
            input_token=f"issue-input:{target}",
        )

    def projection(self):
        from types import SimpleNamespace

        head = self.events[-1]
        return SimpleNamespace(
            epoch_id=self.epoch_id,
            outstanding_target_date=(
                None
                if self.outstanding_target is None
                else date.fromisoformat(self.outstanding_target)
            ),
            issue_events=dict(self.issue_events),
            seal_event=self.seal_event,
            ledger_events=tuple(self.events),
            ledger_event_count=len(self.events),
            ledger_terminal_sequence_id=head.sequence_id,
            ledger_terminal_sha256=head.entry_sha256,
        )


class CalibrationShadowRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.override = mock.patch.dict(os.environ, {shadow.TEST_OVERRIDE_ENV: "1"})
        self.override.start()

    def tearDown(self) -> None:
        self.override.stop()

    @staticmethod
    def _run(root: Path, live: _LiveBuilder) -> shadow.CalibrationShadowResult:
        return shadow.reconcile_calibration_shadow(
            runtime_root=root,
            projection_loader=lambda _profile: live.projection(),
            clock=lambda: FIXED_CLOCK,
        )

    @staticmethod
    def _token(root: Path, live: _LiveBuilder) -> dict[str, object]:
        return shadow.shadow_progress_token_payload(
            runtime_root=root,
            projection_loader=lambda _profile: live.projection(),
        )

    @staticmethod
    def _events(root: Path):
        profile = shadow.load_shadow_profile()
        paths = shadow.runtime_paths(profile, runtime_root=root)
        projection = shadow.load_verified_shadow_projection(profile, paths)
        if projection is None:
            raise AssertionError("expected an activated shadow projection")
        return projection, projection.ledger_events

    def test_profile_bindings_injection_gate_and_cold_readiness(self) -> None:
        profile = shadow.load_shadow_profile()
        self.assertEqual(profile["_profile_sha256"], shadow.DEFAULT_CONFIG_SHA256)
        self.assertEqual(
            hashlib.sha256(shadow.DEFAULT_CONFIG_PATH.read_bytes()).hexdigest(),
            shadow.DEFAULT_CONFIG_SHA256,
        )
        self.assertEqual(shadow.MAX_ACTIONS_PER_RECONCILE, 512)
        self.assertEqual(
            set(profile["bound_artifacts"]),
            {
                "live_profile",
                "deploy_profile",
                "bakeoff_profile",
                "calibration_core",
                "pyproject",
                "uv_lock",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = shadow.shadow_progress_token_payload(
                runtime_root=root,
                projection_loader=lambda _profile: None,
            )
            self.assertFalse(token["shadow_activated"])
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(shadow.CalibrationShadowConfig):
                    shadow.shadow_progress_token_payload(
                        runtime_root=root,
                        projection_loader=lambda _profile: None,
                    )

            live = _LiveBuilder()
            result = self._run(root, live)
            self.assertEqual(result.status, "reconciled")
            readiness = self._token(root, live)["engineering_readiness"]
            self.assertEqual(readiness["overall"], "insufficient_prospective_support")
            self.assertTrue(readiness["evaluation_implemented"])
            self.assertFalse(readiness["promotion_performed"])

    def test_deep_profile_json_is_a_normalized_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deep.json"
            path.write_text('{"child":' * 2_000 + "0" + "}" * 2_000)
            with self.assertRaises(shadow.CalibrationShadowConfig):
                shadow.load_shadow_profile(path)

    def test_issue_is_causal_reveal_is_gated_and_retry_is_idempotent(self) -> None:
        live = _LiveBuilder()
        _, seal = live.add_issue("2030-01-02")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._run(root, live)
            self.assertEqual(first.status, "waiting_for_live_outcome")
            projection, events = self._events(root)
            self.assertEqual(projection.ledger_event_count, 27)
            self.assertEqual(
                sum(event.event_type == "shadow_candidate_issued" for event in events),
                24,
            )
            self.assertTrue(
                all(
                    event.payload["engineering_prospective_ordering_candidate"] is False
                    for event in events
                    if event.event_type == "shadow_candidate_issued"
                )
            )
            self.assertFalse(
                any(event.event_type == "shadow_candidate_revealed" for event in events)
            )
            issue_payloads = [
                event.payload
                for event in events
                if event.event_type in shadow.ISSUE_EVENT_TYPES
            ]
            serialized = json.dumps(issue_payloads, sort_keys=True)
            for forbidden in ("actual_mm", "source_revision_id", "reveal"):
                self.assertNotIn(forbidden, serialized)
            issue_batch_sha = projection.outstanding_issue_batch_sha256
            terminal = projection.ledger_terminal_sha256

            paths = shadow.runtime_paths(
                shadow.load_shadow_profile(), runtime_root=root
            )
            paths.status.unlink()
            retry = self._run(root, live)
            self.assertEqual(retry.status, "waiting_for_live_outcome")
            retried, _ = self._events(root)
            self.assertEqual(retried.ledger_event_count, 27)
            self.assertEqual(retried.ledger_terminal_sha256, terminal)

            live.add_settlement(
                "2030-01-02",
                actual_offset=999.0,
                revision_id="actual-mutation-v1",
                seal_sha256=seal.entry_sha256,
            )
            settled = self._run(root, live)
            self.assertEqual(settled.status, "reconciled")
            final, final_events = self._events(root)
            self.assertEqual(final.ledger_event_count, 77)
            self.assertEqual(final.last_issue_batch_sha256, issue_batch_sha)
            self.assertEqual(
                sum(
                    event.event_type == "shadow_candidate_revealed"
                    for event in final_events
                ),
                24,
            )
            self.assertEqual(final.joint_available_target_dates, 0)
            self.assertTrue(
                all(
                    row["eligible_settled_count"] == 0
                    for method in shadow.METHODS
                    for row in final.sufficient_statistics[method].values()
                )
            )
            self.assertFalse(
                final.eligible_daily[0]["engineering_prospective_ordering_candidate"]
            )
            reloaded = shadow.load_verified_shadow_projection(
                shadow.load_shadow_profile(), paths
            )
            self.assertIsNotNone(reloaded)
            self.assertEqual(
                shadow._state_hashes(
                    final.states, shadow._core(shadow.load_shadow_profile())
                ),
                shadow._state_hashes(
                    reloaded.states, shadow._core(shadow.load_shadow_profile())
                ),
            )
            settled_terminal = final.ledger_terminal_sha256
            paths.status.unlink()
            recovered = self._run(root, live)
            self.assertEqual(
                recovered.projection.ledger_terminal_sha256, settled_terminal
            )
            self.assertEqual(recovered.projection.ledger_event_count, 77)

    def test_activation_history_is_cut_off_and_two_day_backlog_is_complete(
        self,
    ) -> None:
        live = _LiveBuilder()
        live.add_settlement("2030-01-01", revision_id="historical-v1")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, live)
            projection, events = self._events(root)
            self.assertEqual(projection.ledger_event_count, 1)
            self.assertFalse(
                any(
                    event.event_type == "shadow_backfill_ineligible" for event in events
                )
            )
            state_before = self._token(root, live)["method_station_state_sha256"]

            live.add_settlement("2030-01-02", revision_id="missed-v2")
            live.add_settlement("2030-01-03", revision_id="missed-v3")
            result = self._run(root, live)
            self.assertEqual(result.status, "reconciled")
            caught_up, caught_events = self._events(root)
            self.assertEqual(
                set(caught_up.backfill_ineligible),
                {"2030-01-02", "2030-01-03"},
            )
            self.assertEqual(
                sum(
                    event.event_type == "shadow_backfill_ineligible"
                    for event in caught_events
                ),
                2,
            )
            self.assertFalse(
                any(
                    event.event_type == "shadow_state_updated"
                    for event in caught_events
                )
            )
            self.assertEqual(
                state_before,
                self._token(root, live)["method_station_state_sha256"],
            )

    def test_revision_uses_hashed_key_and_never_updates_online_state(self) -> None:
        live = _LiveBuilder()
        _, seal = live.add_issue("2030-01-02")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, live)
            live.add_settlement("2030-01-02", seal_sha256=seal.entry_sha256)
            self._run(root, live)
            before = self._token(root, live)
            revision_id = "revision:/colon:unicode-α/" + "x" * 400
            live.add_revision("2030-01-02", revision_id, actual_offset=5.0)
            self._run(root, live)
            after = self._token(root, live)
            projection, events = self._events(root)
            revision_events = [
                event
                for event in events
                if event.event_type == "shadow_outcome_revision_rescored"
            ]
            self.assertEqual(len(revision_events), 24)
            self.assertTrue(
                all(revision_id not in event.event_key for event in revision_events)
            )
            self.assertTrue(
                all(
                    event.payload["source_revision_id"] == revision_id
                    and event.state_before_sha256 == event.state_after_sha256
                    and event.payload["online_state_updated"] is False
                    and event.payload["eligible_metrics_updated"] is False
                    for event in revision_events
                )
            )
            self.assertEqual(
                before["method_station_state_sha256"],
                after["method_station_state_sha256"],
            )
            self.assertEqual(
                before["sufficient_statistics"], after["sufficient_statistics"]
            )
            self.assertIn(revision_id, projection.revisions["2030-01-02"])

            second_id = revision_id + ":distinct"
            live.add_revision("2030-01-02", second_id, actual_offset=7.0)
            self._run(root, live)
            _, twice = self._events(root)
            keys = [
                event.event_key
                for event in twice
                if event.event_type == "shadow_outcome_revision_rescored"
            ]
            self.assertEqual(len(keys), len(set(keys)))

    def test_post_activation_issue_is_engineering_candidate_and_drift_resets(
        self,
    ) -> None:
        live = _LiveBuilder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, live)
            _, seal = live.add_issue("2030-01-02")
            issued = self._run(root, live)
            self.assertEqual(issued.status, "waiting_for_live_outcome")
            projection, events = self._events(root)
            self.assertTrue(projection.outstanding_prospective_eligible)
            candidates = [
                event
                for event in events
                if event.event_type == "shadow_candidate_issued"
            ]
            self.assertEqual(len(candidates), 24)
            self.assertTrue(
                all(
                    event.payload["engineering_prospective_ordering_candidate"] is True
                    for event in candidates
                )
            )

            live.add_settlement(
                "2030-01-02",
                seal_sha256=seal.entry_sha256,
                drift_stations=("ATU1",),
            )
            self._run(root, live)
            settled, settled_events = self._events(root)
            for method in shadow.METHODS:
                self.assertEqual(
                    settled.states[method]["ATU1"].reset_reason,
                    "drift_detected_previous_date",
                )
                self.assertEqual(settled.states[method]["ATU1"].history_count, 0)
            reset_rows = [
                event
                for event in settled_events
                if event.event_type == "shadow_state_updated"
                and event.station == "ATU1"
            ]
            self.assertEqual(len(reset_rows), 3)
            self.assertTrue(
                all(
                    event.payload["live_drift_detected"] is True for event in reset_rows
                )
            )

    def test_tamper_blocks_without_append_and_replaces_stale_success_status(
        self,
    ) -> None:
        live = _LiveBuilder()
        live.add_issue("2030-01-02")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, live)
            profile = shadow.load_shadow_profile()
            paths = shadow.runtime_paths(profile, runtime_root=root)
            before_count = (
                sqlite3.connect(paths.ledger)
                .execute("SELECT COUNT(*) FROM events")
                .fetchone()[0]
            )
            connection = sqlite3.connect(paths.ledger)
            try:
                triggers = connection.execute(
                    "SELECT name, sql FROM sqlite_schema "
                    "WHERE type='trigger' AND tbl_name='events' ORDER BY name"
                ).fetchall()
                connection.execute("BEGIN IMMEDIATE")
                for name, _sql in triggers:
                    connection.execute(f'DROP TRIGGER "{name}"')
                connection.execute(
                    "UPDATE events SET payload_json='{}' WHERE sequence_id=1"
                )
                for _name, sql in triggers:
                    connection.execute(sql)
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(shadow.CalibrationShadowIntegrity):
                self._run(root, live)
            connection = sqlite3.connect(paths.ledger)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    before_count,
                )
            finally:
                connection.close()
            status = json.loads(paths.status.read_text(encoding="utf-8"))
            self.assertEqual(status["runner_status"], "blocked_integrity")
            self.assertFalse(status["promotion_performed"])

    def test_progress_ignores_anchor_churn_and_shadow_recorded_time(self) -> None:
        live = _LiveBuilder()
        live.add_issue("2030-01-02")
        with (
            tempfile.TemporaryDirectory() as first_dir,
            tempfile.TemporaryDirectory() as second_dir,
        ):
            first_root = Path(first_dir)
            second_root = Path(second_dir)
            with mock.patch.object(
                ledger_module,
                "_utc_now_text",
                return_value="2030-01-01T00:00:00.000001Z",
            ):
                self._run(first_root, live)
            with mock.patch.object(
                ledger_module,
                "_utc_now_text",
                return_value="2030-01-01T00:00:01.999999Z",
            ):
                self._run(second_root, live)
            first_token = self._token(first_root, live)
            second_token = self._token(second_root, live)
            self.assertEqual(first_token, second_token)
            self.assertNotIn("shadow_ledger_terminal_sha256", first_token)
            before = dict(first_token)
            live.add_anchor_failure("2030-01-02")
            after = self._token(first_root, live)
            self.assertEqual(before, after)

    def test_lock_contention_and_status_oserror_are_normalized(self) -> None:
        live = _LiveBuilder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, live)
            paths = shadow.runtime_paths(
                shadow.load_shadow_profile(), runtime_root=root
            )
            success_status = paths.status.read_bytes()
            root.mkdir(parents=True, exist_ok=True)
            lock_path = root / "test_upstream_runner.lock"
            with lock_path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(shadow.CalibrationShadowBusy):
                    self._run(root, live)
                self.assertEqual(paths.status.read_bytes(), success_status)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

            with mock.patch.object(
                shadow.tempfile,
                "mkstemp",
                side_effect=OSError("test status failure"),
            ):
                with self.assertRaises(shadow.CalibrationShadowIntegrity):
                    shadow.reconcile_calibration_shadow(
                        runtime_root=Path(directory) / "status-error-runtime",
                        projection_loader=lambda _profile: None,
                        clock=lambda: FIXED_CLOCK,
                    )

    def test_bounded_backlog_returns_work_remaining_then_continues(self) -> None:
        live = _LiveBuilder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, live)
            for day in (2, 3, 4):
                live.add_backfill(f"2030-01-0{day}")
            with mock.patch.object(shadow, "MAX_ACTIONS_PER_RECONCILE", 2):
                partial = self._run(root, live)
                self.assertEqual(partial.status, "work_remaining")
                projection, _ = self._events(root)
                self.assertEqual(len(projection.backfill_ineligible), 2)

                completed = self._run(root, live)
                self.assertEqual(completed.status, "reconciled")
                projection, _ = self._events(root)
                self.assertEqual(
                    set(projection.backfill_ineligible),
                    {"2030-01-02", "2030-01-03", "2030-01-04"},
                )

    def test_epoch_rotation_is_automatic_only_without_outstanding_issue(self) -> None:
        live = _LiveBuilder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = self._run(root, live).projection
            self.assertIsNotNone(initial)
            initial_epoch = initial.shadow_epoch_id
            live.epoch_id = "verified-live-epoch-v2"
            rotated = self._run(root, live).projection
            self.assertIsNotNone(rotated)
            self.assertNotEqual(rotated.shadow_epoch_id, initial_epoch)
            self.assertEqual(rotated.upstream_live_epoch_id, live.epoch_id)
            _, events = self._events(root)
            self.assertEqual(
                [event.event_type for event in events[-2:]],
                ["shadow_epoch_closed", "shadow_epoch_genesis"],
            )
            self.assertTrue(
                all(
                    state.history_count == 0
                    and state.reset_reason == "initial_fold_start"
                    for method in shadow.METHODS
                    for state in rotated.states[method].values()
                )
            )

        blocked_live = _LiveBuilder()
        blocked_live.add_issue("2030-01-02")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = self._run(root, blocked_live).projection
            self.assertIsNotNone(before)
            terminal = before.ledger_terminal_sha256
            blocked_live.epoch_id = "verified-live-epoch-v2"
            with self.assertRaises(shadow.CalibrationShadowIntegrity):
                self._run(root, blocked_live)
            profile = shadow.load_shadow_profile()
            projection = shadow.load_verified_shadow_projection(
                profile, shadow.runtime_paths(profile, runtime_root=root)
            )
            self.assertIsNotNone(projection)
            self.assertEqual(projection.ledger_terminal_sha256, terminal)

    def test_interleaved_revision_is_not_skipped_by_later_source_cursor(self) -> None:
        live = _LiveBuilder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, live)
            _, first_seal = live.add_issue("2030-01-02")
            self._run(root, live)
            live.add_settlement("2030-01-02", seal_sha256=first_seal.entry_sha256)
            self._run(root, live)

            _, second_seal = live.add_issue("2030-01-03")
            self._run(root, live)
            first_revision = "revision-before-outstanding-settlement"
            live.add_revision("2030-01-02", first_revision, actual_offset=3.0)
            live.add_settlement("2030-01-03", seal_sha256=second_seal.entry_sha256)
            self._run(root, live)
            projection, events = self._events(root)
            self.assertIn(first_revision, projection.revisions["2030-01-02"])
            self.assertIn("2030-01-03", projection.settled)
            revision_sequences = [
                event.sequence_id
                for event in events
                if event.event_type == "shadow_outcome_revision_rescored"
                and event.payload["source_revision_id"] == first_revision
            ]
            second_settlement = next(
                event
                for event in events
                if event.event_type == "shadow_outcome_batch_settled"
                and event.target_date == "2030-01-03"
            )
            self.assertLess(max(revision_sequences), second_settlement.sequence_id)

            live.add_backfill("2030-01-04")
            second_revision = "revision-between-two-backfills"
            live.add_revision("2030-01-02", second_revision, actual_offset=4.0)
            live.add_backfill("2030-01-05")
            self._run(root, live)
            projection, events = self._events(root)
            self.assertEqual(
                {"2030-01-04", "2030-01-05"}.intersection(
                    projection.backfill_ineligible
                ),
                {"2030-01-04", "2030-01-05"},
            )
            backfill_sequences = {
                event.target_date: event.sequence_id
                for event in events
                if event.event_type == "shadow_backfill_ineligible"
                and event.target_date in {"2030-01-04", "2030-01-05"}
            }
            interleaved_revision_sequences = [
                event.sequence_id
                for event in events
                if event.event_type == "shadow_outcome_revision_rescored"
                and event.payload["source_revision_id"] == second_revision
            ]
            self.assertLess(
                backfill_sequences["2030-01-04"],
                min(interleaved_revision_sequences),
            )
            self.assertLess(
                max(interleaved_revision_sequences),
                backfill_sequences["2030-01-05"],
            )

            live.add_issue("2030-01-06")
            revision_after_seal = "revision-after-new-seal"
            live.add_revision("2030-01-02", revision_after_seal, actual_offset=6.0)
            self._run(root, live)
            _, events = self._events(root)
            new_issue_sequence = next(
                event.sequence_id
                for event in events
                if event.event_type == "shadow_issue_batch_opened"
                and event.target_date == "2030-01-06"
            )
            after_seal_revision_sequences = [
                event.sequence_id
                for event in events
                if event.event_type == "shadow_outcome_revision_rescored"
                and event.payload["source_revision_id"] == revision_after_seal
            ]
            self.assertLess(new_issue_sequence, min(after_seal_revision_sequences))

    def test_offline_settlement_then_new_live_seal_settles_before_issuing(self) -> None:
        live = _LiveBuilder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._run(root, live)
            _, first_seal = live.add_issue("2030-01-02")
            self._run(root, live)

            live.add_settlement("2030-01-02", seal_sha256=first_seal.entry_sha256)
            _, second_seal = live.add_issue("2030-01-03")
            result = self._run(root, live)
            self.assertEqual(result.status, "waiting_for_live_outcome")
            projection, events = self._events(root)
            self.assertIn("2030-01-02", projection.settled)
            self.assertEqual(projection.outstanding_target_date, "2030-01-03")
            self.assertEqual(
                projection.outstanding_live_seal_entry_sha256,
                second_seal.entry_sha256,
            )
            self.assertEqual(
                projection.live_cursor_sequence_id, second_seal.sequence_id
            )
            first_settlement_sequence = next(
                event.sequence_id
                for event in events
                if event.event_type == "shadow_outcome_batch_settled"
                and event.target_date == "2030-01-02"
            )
            second_issue_sequence = next(
                event.sequence_id
                for event in events
                if event.event_type == "shadow_issue_batch_opened"
                and event.target_date == "2030-01-03"
            )
            self.assertLess(first_settlement_sequence, second_issue_sequence)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
