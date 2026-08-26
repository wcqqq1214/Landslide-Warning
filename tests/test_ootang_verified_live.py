"""Contracts for the additive independently verified live entrypoint."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
TESTS_DIR = ROOT / "tests"
for directory in (CODE_DIR, TESTS_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from monitoring import ootang_prequential_live as live  # noqa: E402
from monitoring import ootang_live_ledger as live_ledger  # noqa: E402
from monitoring import ootang_prequential_cycle_v3 as cycle_v3  # noqa: E402
from monitoring import ootang_verified_live as guard  # noqa: E402
from test_ootang_prequential_live import (  # noqa: E402
    FIRST_TARGET,
    ISSUE_CLOCK,
    OUTCOME_CLOCK,
    SECOND_ISSUE_CLOCK,
    _LiveFixture,
)


class VerifiedLiveFixture(_LiveFixture):
    def __init__(self) -> None:
        super().__init__()
        self.guard_profile = guard.load_verified_live_profile()
        self.guard_paths = guard.runtime_paths(
            self.guard_profile, runtime_root=self.root
        )

    def guard_poll(self, *, now=ISSUE_CLOCK) -> Path:
        with mock.patch.object(
            live_ledger, "_utc_now_text", return_value=guard._utc_text(now)
        ):
            return guard.poll_verified_live_runner(
                runtime_root=self.root, clock=lambda: now
            )

    def fake_replay(
        self,
        paths: guard.GuardRuntimePaths,
        **_kwargs: object,
    ) -> tuple[date, guard.ArtifactSnapshot]:
        profile = live.load_config()
        prerequisites = live.load_prerequisites(profile, paths.live)
        assert prerequisites is not None
        events = live.AppendOnlyLedger(paths.live.ledger).read_events()
        projection = live._reconstruct_projection(events, profile, prerequisites)
        target = projection.last_finalized_date.fromordinal(
            projection.last_finalized_date.toordinal() + 1
        )
        issue_path = paths.live.issue_inbox / f"{target.isoformat()}.json"
        issue = live.load_issue_batch(issue_path, profile, prerequisites)
        issue_snapshot = guard._issue_snapshot(issue)
        input_snapshot = guard._input_snapshot(issue)
        payload = {
            "schema_version": "ootang_issue_replay_receipt_v1",
            "profile_id": "ootang-issue-replay-v1",
            "artifact_status": (
                "e2_checkpoint_input_replay_engineering_only_not_live_evidence"
            ),
            "target_date": target.isoformat(),
            "verified_at_utc": "2030-01-01T15:45:00.000000Z",
            "ledger_pre_head": guard._pre_head(events, projection.epoch_id),
            "issue": {
                "path": str(issue_snapshot.path),
                "sha256": issue_snapshot.sha256,
                "size_bytes": issue_snapshot.size_bytes,
            },
            "input_manifest": {
                "path": str(input_snapshot.path),
                "sha256": input_snapshot.sha256,
                "size_bytes": input_snapshot.size_bytes,
            },
            "source": {},
            "model": {},
            "verification": {"all_40_seed_predictions_match": True},
            "implementation": {},
            "formal_warning_output": False,
            "e2_live_evidence_eligible": False,
            "real_activation_ready": False,
            "automatic_calibration_promotion": False,
        }
        receipt_path = (
            paths.root / "issue_replay_receipts" / f"{target.isoformat()}.json"
        )
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(guard._canonical_bytes(payload))
        return target, guard._read_snapshot(
            receipt_path, name="fake independent replay receipt"
        )


class VerifiedLiveTests(unittest.TestCase):
    def setUp(self) -> None:
        def fake_public_loader(
            paths: guard.GuardRuntimePaths, *, target: date, **_kwargs: object
        ) -> guard.ArtifactSnapshot:
            return guard._read_snapshot(
                paths.root / "issue_replay_receipts" / f"{target.isoformat()}.json",
                name="fake public replay receipt",
            )

        self._public_loader_patch = mock.patch.object(
            guard,
            "_load_public_replay_receipt",
            side_effect=fake_public_loader,
        )
        self._public_loader_patch.start()

    def tearDown(self) -> None:
        self._public_loader_patch.stop()

    @staticmethod
    def _rebase_guard_chain_with_different_times(runtime_root: Path) -> None:
        profile = guard.load_verified_live_profile()
        paths = guard.runtime_paths(profile, runtime_root=runtime_root)
        receipt_path = (
            runtime_root
            / "issue_replay_receipts"
            / f"{FIRST_TARGET.isoformat()}.json"
        )
        intent_path = paths.intents / f"{FIRST_TARGET.isoformat()}.json"
        completion_path = paths.completions / f"{FIRST_TARGET.isoformat()}.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        completion = json.loads(completion_path.read_text(encoding="utf-8"))

        issue = guard._validate_artifact_reference(
            intent["issue"], root=paths.root, name="cloned intent.issue"
        )
        input_manifest = guard._validate_artifact_reference(
            intent["input_manifest"],
            root=paths.root,
            name="cloned intent.input_manifest",
        )
        receipt["verified_at_utc"] = "2030-01-01T15:44:00.000000Z"
        receipt["issue"]["path"] = str(issue.path)
        receipt["input_manifest"]["path"] = str(input_manifest.path)
        guard._atomic_write(receipt_path, guard._canonical_bytes(receipt))
        receipt_snapshot = guard._read_snapshot(
            receipt_path, name="cloned replay receipt"
        )

        intent["created_at_utc"] = "2030-01-01T15:44:30.000000Z"
        intent["replay_receipt"] = receipt_snapshot.reference(runtime_root)
        guard._atomic_write(intent_path, guard._canonical_bytes(intent))
        intent_snapshot = guard._read_snapshot(intent_path, name="cloned guard intent")

        completion["completed_at_utc"] = "2030-01-01T15:45:30.000000Z"
        completion["intent"] = intent_snapshot.reference(runtime_root)
        completion["replay_receipt"] = receipt_snapshot.reference(runtime_root)
        guard._atomic_write(completion_path, guard._canonical_bytes(completion))

    @staticmethod
    def _cycle_token(runtime_root: Path) -> str:
        profile = cycle_v3.load_cycle_v3_profile()
        paths = cycle_v3.runtime_paths_v3(
            profile,
            runtime_root=runtime_root,
            shadow_runtime_root=runtime_root / "semantic-shadow",
        )
        return cycle_v3.build_progress_token_v3(
            paths,
            profile,
            v2_token_builder=lambda _paths, _profile: "a" * 64,
            replay_payload_builder=lambda **_kwargs: {
                "replay_semantic_sha256": "b" * 64
            },
            guard_payload_builder=lambda **kwargs: guard.guard_progress_payload(
                config_path=kwargs["config_path"],
                runtime_root=kwargs["runtime_root"],
                project_root=kwargs["project_root"],
            ),
        )

    def test_default_profile_hash_and_false_evidence_boundary(self) -> None:
        profile = guard.load_verified_live_profile()
        self.assertEqual(profile["_profile_sha256"], guard.DEFAULT_CONFIG_SHA256)
        self.assertFalse(profile["formal_warning_output"])
        self.assertFalse(
            profile["engineering_capabilities"]["e2_live_evidence_eligible"]
        )
        self.assertFalse(profile["protocol"]["old_live_v1_entrypoint_disabled"])

    def test_missing_prerequisites_wait_without_live_ledger(self) -> None:
        with VerifiedLiveFixture() as fixture:
            status_path = fixture.guard_poll()
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["runner_status"], "waiting_for_live_prerequisites")
            self.assertFalse(fixture.paths.ledger.exists())

    def test_one_poll_creates_only_genesis(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            status = json.loads(fixture.guard_poll().read_text(encoding="utf-8"))
            self.assertEqual(status["runner_status"], "work_remaining")
            self.assertEqual(
                [event.event_type for event in fixture.events()], ["epoch_genesis"]
            )

    def test_replay_failure_prevents_intent_and_live_issue(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with mock.patch.object(
                guard,
                "_run_replay_under_lock",
                side_effect=guard.VerifiedLiveIntegrityError("prediction mismatch"),
            ):
                with self.assertRaises(guard.VerifiedLiveIntegrityError):
                    fixture.guard_poll()
            self.assertEqual(
                [event.event_type for event in fixture.events()], ["epoch_genesis"]
            )
            self.assertFalse(
                (fixture.guard_paths.intents / f"{FIRST_TARGET}.json").exists()
            )
            status = json.loads(fixture.guard_paths.status.read_text(encoding="utf-8"))
            self.assertEqual(status["runner_status"], "blocked_integrity")

    def test_public_receipt_validation_fails_before_intent_and_live_issue(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with (
                mock.patch.object(
                    guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
                ),
                mock.patch.object(
                    guard,
                    "_load_public_replay_receipt",
                    side_effect=guard.VerifiedLiveIntegrityError(
                        "receipt model binding changed"
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    guard.VerifiedLiveIntegrityError, "model binding"
                ):
                    fixture.guard_poll()
            self.assertEqual(
                [event.event_type for event in fixture.events()], ["epoch_genesis"]
            )
            self.assertFalse(
                (fixture.guard_paths.intents / f"{FIRST_TARGET}.json").exists()
            )

    def test_crash_after_intent_before_live_append_is_idempotently_recovered(
        self,
    ) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with (
                mock.patch.object(
                    guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
                ),
                mock.patch.object(
                    live, "_append_issue_batch", side_effect=RuntimeError("crash A")
                ),
            ):
                with self.assertRaises(guard.VerifiedLiveIntegrityError):
                    fixture.guard_poll()
            intent_path = fixture.guard_paths.intents / f"{FIRST_TARGET}.json"
            self.assertTrue(intent_path.is_file())
            intent_bytes = intent_path.read_bytes()
            self.assertEqual(
                [event.event_type for event in fixture.events()], ["epoch_genesis"]
            )

            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
            ):
                status = json.loads(fixture.guard_poll().read_text(encoding="utf-8"))
            self.assertEqual(status["runner_status"], "work_remaining")
            self.assertEqual(intent_path.read_bytes(), intent_bytes)
            self.assertTrue(
                (fixture.guard_paths.completions / f"{FIRST_TARGET}.json").is_file()
            )

    def test_crash_after_live_append_recovers_completion_before_outcome(self) -> None:
        class SimulatedProcessDeath(BaseException):
            pass

        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with (
                mock.patch.object(
                    guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
                ),
                mock.patch.object(
                    guard,
                    "_ensure_completion",
                    side_effect=SimulatedProcessDeath(),
                ),
            ):
                with self.assertRaises(SimulatedProcessDeath):
                    fixture.guard_poll()
            self.assertIn(
                "issue_batch_sealed", [event.event_type for event in fixture.events()]
            )
            completion_path = fixture.guard_paths.completions / f"{FIRST_TARGET}.json"
            self.assertFalse(completion_path.exists())

            fixture.write_outcome()
            with mock.patch.object(
                live,
                "load_outcome_batch",
                side_effect=AssertionError(
                    "outcome was read before B-crash completion recovery"
                ),
            ):
                status = json.loads(
                    fixture.guard_poll(now=OUTCOME_CLOCK).read_text(encoding="utf-8")
                )
            self.assertEqual(status["runner_status"], "work_remaining")
            self.assertTrue(completion_path.is_file())
            completion = json.loads(completion_path.read_text(encoding="utf-8"))
            seal = next(
                event
                for event in fixture.events()
                if event.event_type == "issue_batch_sealed"
            )
            self.assertGreaterEqual(
                guard._parse_record_utc(
                    completion["completed_at_utc"],
                    name="test recovery completion",
                ),
                guard._parse_record_utc(
                    seal.recorded_at_utc,
                    name="test recovery seal",
                ),
            )
            self.assertNotIn(
                "outcome_batch_opened",
                [event.event_type for event in fixture.events()],
            )

    def test_crash_after_live_append_with_regressed_clock_writes_no_completion(
        self,
    ) -> None:
        class SimulatedProcessDeath(BaseException):
            pass

        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with (
                mock.patch.object(
                    guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
                ),
                mock.patch.object(
                    guard,
                    "_ensure_completion",
                    side_effect=SimulatedProcessDeath(),
                ),
            ):
                with self.assertRaises(SimulatedProcessDeath):
                    fixture.guard_poll()

            completion_path = fixture.guard_paths.completions / f"{FIRST_TARGET}.json"
            self.assertFalse(completion_path.exists())
            fixture.write_outcome()
            regressed = ISSUE_CLOCK - timedelta(seconds=1)
            with mock.patch.object(
                live,
                "load_outcome_batch",
                side_effect=AssertionError(
                    "outcome was read before regressed-clock recovery failed closed"
                ),
            ):
                with self.assertRaisesRegex(
                    guard.VerifiedLiveIntegrityError,
                    "machine time predates its intent or live issue seal",
                ):
                    fixture.guard_poll(now=regressed)
            self.assertFalse(completion_path.exists())
            self.assertNotIn(
                "outcome_batch_opened",
                [event.event_type for event in fixture.events()],
            )

    def test_live_seal_at_target_start_never_gets_guard_completion(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            target_start_utc = datetime(2030, 1, 1, 16, 0, tzinfo=timezone.utc)
            crossing_clock = mock.Mock(
                side_effect=[
                    ISSUE_CLOCK,
                    ISSUE_CLOCK,
                    ISSUE_CLOCK,
                    target_start_utc,
                ]
            )
            with (
                mock.patch.object(
                    guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
                ),
                mock.patch.object(
                    live_ledger,
                    "_utc_now_text",
                    return_value="2030-01-01T16:00:00.000000Z",
                ),
            ):
                with self.assertRaisesRegex(
                    guard.VerifiedLiveIntegrityError,
                    "pre-target guard intent boundary",
                ):
                    guard.poll_verified_live_runner(
                        runtime_root=fixture.root,
                        clock=crossing_clock,
                    )
            self.assertIn(
                "issue_batch_sealed", [event.event_type for event in fixture.events()]
            )
            completion_path = fixture.guard_paths.completions / f"{FIRST_TARGET}.json"
            self.assertFalse(completion_path.exists())

            fixture.write_outcome()
            with mock.patch.object(
                live,
                "load_outcome_batch",
                side_effect=AssertionError(
                    "outcome was read after a causally invalid live seal"
                ),
            ):
                with self.assertRaisesRegex(
                    guard.VerifiedLiveIntegrityError,
                    "pre-target guard intent boundary",
                ):
                    fixture.guard_poll()
            self.assertFalse(completion_path.exists())
            self.assertNotIn(
                "outcome_batch_opened",
                [event.event_type for event in fixture.events()],
            )

    def test_direct_v1_issue_is_never_retroactively_authorized(self) -> None:
        with (
            VerifiedLiveFixture() as fixture,
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            fixture.poll()
            self.assertIn(
                "issue_batch_sealed", [event.event_type for event in fixture.events()]
            )
            with self.assertRaisesRegex(
                guard.VerifiedLiveIntegrityError, "retroactive authorization"
            ):
                fixture.guard_poll()
            self.assertFalse(
                (fixture.guard_paths.completions / f"{FIRST_TARGET}.json").exists()
            )

    def test_completion_is_durable_before_matching_outcome_is_read(self) -> None:
        with (
            VerifiedLiveFixture() as fixture,
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
            ):
                fixture.guard_poll()
            completion = fixture.guard_paths.completions / f"{FIRST_TARGET}.json"
            self.assertTrue(completion.is_file())
            completion_payload = json.loads(completion.read_text(encoding="utf-8"))
            seal = next(
                event
                for event in fixture.events()
                if event.event_type == "issue_batch_sealed"
            )
            self.assertGreaterEqual(
                guard._parse_record_utc(
                    completion_payload["completed_at_utc"],
                    name="test normal completion",
                ),
                guard._parse_record_utc(
                    seal.recorded_at_utc,
                    name="test normal seal",
                ),
            )
            fixture.write_outcome()
            original_loader = live.load_outcome_batch

            def completion_checked_loader(*args: object, **kwargs: object):
                self.assertTrue(completion.is_file())
                return original_loader(*args, **kwargs)

            with mock.patch.object(
                live,
                "load_outcome_batch",
                side_effect=completion_checked_loader,
            ):
                # The first call records the bounded anchor attempt; the second
                # performs exactly one outcome settlement.
                fixture.guard_poll(now=OUTCOME_CLOCK)
                fixture.guard_poll(now=OUTCOME_CLOCK)
            event_types = [event.event_type for event in fixture.events()]
            self.assertIn("outcome_batch_settled", event_types)

    def test_issue_toctou_after_replay_is_blocked_before_intent(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            issue_path = fixture.write_issue()

            def replay_then_change(*args: object, **kwargs: object):
                result = fixture.fake_replay(*args, **kwargs)
                payload = json.loads(issue_path.read_text(encoding="utf-8"))
                payload["stations"][0]["seed0_p50_mm"] += 1.0
                fixture._write_json(issue_path, payload)
                return result

            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=replay_then_change
            ):
                with self.assertRaisesRegex(
                    guard.VerifiedLiveIntegrityError,
                    "changed during independent replay",
                ):
                    fixture.guard_poll()
            self.assertEqual(
                [event.event_type for event in fixture.events()], ["epoch_genesis"]
            )
            self.assertFalse(
                (fixture.guard_paths.intents / f"{FIRST_TARGET}.json").exists()
            )

    def test_machine_time_crossing_target_start_blocks_before_intent(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            target_start_utc = datetime(2030, 1, 1, 16, 0, tzinfo=timezone.utc)
            clock = mock.Mock(side_effect=[ISSUE_CLOCK, target_start_utc])
            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
            ):
                status_path = guard.poll_verified_live_runner(
                    runtime_root=fixture.root,
                    clock=clock,
                )
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["runner_status"], "waiting_for_backfill_outcome")
            self.assertEqual(
                status["reason"],
                "pre_intent_machine_time_fence_rejected_target_day",
            )
            self.assertEqual(clock.call_count, 2)
            self.assertEqual(
                [event.event_type for event in fixture.events()], ["epoch_genesis"]
            )
            self.assertFalse(
                (fixture.guard_paths.intents / f"{FIRST_TARGET}.json").exists()
            )

    def test_machine_time_regression_fails_closed_before_intent(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            clock = mock.Mock(
                side_effect=[ISSUE_CLOCK, ISSUE_CLOCK - timedelta(seconds=1)]
            )
            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
            ):
                with self.assertRaisesRegex(
                    guard.VerifiedLiveIntegrityError, "clock regressed"
                ):
                    guard.poll_verified_live_runner(
                        runtime_root=fixture.root,
                        clock=clock,
                    )
            self.assertEqual(
                [event.event_type for event in fixture.events()], ["epoch_genesis"]
            )
            self.assertFalse(
                (fixture.guard_paths.intents / f"{FIRST_TARGET}.json").exists()
            )

    def test_runner_lock_busy_does_not_overwrite_status(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            status_before = fixture.guard_paths.status.read_bytes()
            handle = fixture.paths.lock.open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with self.assertRaises(guard.VerifiedLiveBusyError):
                    fixture.guard_poll()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self.assertEqual(fixture.guard_paths.status.read_bytes(), status_before)

    def test_runner_lock_final_symlink_is_rejected_without_locking_target(self) -> None:
        with VerifiedLiveFixture() as fixture:
            target = fixture.root / "runner-lock-target"
            target.write_bytes(b"")
            fixture.paths.lock.symlink_to(target)
            with mock.patch.object(guard, "_poll_locked") as poll_locked:
                with self.assertRaisesRegex(
                    guard.VerifiedLiveIntegrityError, "safely open live runner lock"
                ):
                    fixture.guard_poll()
            poll_locked.assert_not_called()
            with target.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_final_component_symlink_is_never_followed_for_guard_records(self) -> None:
        with VerifiedLiveFixture() as fixture:
            target = fixture.root / "outside.json"
            target.write_text("{}\n", encoding="utf-8")
            alias = fixture.root / "alias.json"
            alias.symlink_to(target)
            with self.assertRaisesRegex(
                guard.VerifiedLiveIntegrityError, "cannot be read"
            ):
                guard._read_snapshot(alias, name="symlinked guard record")

    def test_outcome_final_symlink_is_rejected_before_backfill(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            outcome_path = fixture.write_outcome()
            symlink_target = fixture.root / "outside-outcome.json"
            os.replace(outcome_path, symlink_target)
            outcome_path.symlink_to(symlink_target)

            with self.assertRaisesRegex(
                guard.VerifiedLiveIntegrityError, "live outcome batch cannot be read"
            ):
                fixture.guard_poll(now=OUTCOME_CLOCK)
            self.assertEqual(
                [event.event_type for event in fixture.events()], ["epoch_genesis"]
            )

    def test_outcome_pathname_replacement_during_parse_is_rejected(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_outcome()
            replacement = fixture.root / "replacement-outcome.json"
            fixture._write_json(
                replacement,
                fixture.outcome_payload(
                    FIRST_TARGET,
                    revision_id="replacement-revision",
                    actual_offset=2.0,
                ),
            )
            original_loader = live.load_outcome_batch

            def parse_then_replace(
                path: Path,
                profile: dict[str, object],
                prerequisites: live.Prerequisites,
            ) -> live.OutcomeBatch:
                outcome = original_loader(path, profile, prerequisites)
                os.replace(replacement, path)
                return outcome

            with mock.patch.object(
                live, "load_outcome_batch", side_effect=parse_then_replace
            ):
                with self.assertRaisesRegex(
                    guard.VerifiedLiveIntegrityError,
                    "changed while guarded parsing was in progress",
                ):
                    fixture.guard_poll(now=OUTCOME_CLOCK)
            self.assertEqual(
                [event.event_type for event in fixture.events()], ["epoch_genesis"]
            )

    def test_snapshot_rejects_final_pathname_replacement_race(self) -> None:
        with VerifiedLiveFixture() as fixture:
            path = fixture.root / "guard-record.json"
            replacement = fixture.root / "replacement.json"
            path.write_text('{"version":1}\n', encoding="utf-8")
            replacement.write_text('{"version":2}\n', encoding="utf-8")
            original_lstat = os.lstat
            replaced = False

            def replace_after_first_lstat(candidate: os.PathLike[str] | str):
                nonlocal replaced
                result = original_lstat(candidate)
                if Path(candidate) == path and not replaced:
                    replaced = True
                    os.replace(replacement, path)
                return result

            with mock.patch.object(
                guard.os, "lstat", side_effect=replace_after_first_lstat
            ):
                with self.assertRaisesRegex(
                    guard.VerifiedLiveIntegrityError,
                    "pathname changed while being captured",
                ):
                    guard._read_snapshot(path, name="raced guard record")
            self.assertTrue(replaced)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"version":2}\n')

    def test_snapshot_size_is_bounded_before_read(self) -> None:
        with VerifiedLiveFixture() as fixture:
            oversized = fixture.root / "oversized-record.json"
            with oversized.open("wb") as handle:
                handle.truncate(guard.MAX_SNAPSHOT_BYTES + 1)
            with self.assertRaisesRegex(
                guard.VerifiedLiveIntegrityError, "snapshot size limit"
            ):
                guard._read_snapshot(oversized, name="oversized guard record")

    def test_guard_records_and_progress_are_byte_idempotent(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
            ):
                fixture.guard_poll()
            intent = fixture.guard_paths.intents / f"{FIRST_TARGET}.json"
            completion = fixture.guard_paths.completions / f"{FIRST_TARGET}.json"
            before = (intent.read_bytes(), completion.read_bytes())
            progress_before = guard.guard_progress_payload(runtime_root=fixture.root)
            with mock.patch.dict(os.environ, {}, clear=True):
                anchor_status = json.loads(
                    fixture.guard_poll().read_text(encoding="utf-8")
                )
            progress_after = guard.guard_progress_payload(runtime_root=fixture.root)
            self.assertEqual(anchor_status["runner_status"], "waiting_for_outcome")
            self.assertEqual((intent.read_bytes(), completion.read_bytes()), before)
            self.assertEqual(progress_after, progress_before)
            self.assertEqual(progress_before["completion_count"], 1)
            self.assertFalse(progress_before["e2_live_evidence_eligible"])

    def test_create_scratch_residue_does_not_block_machine_progress(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
            ):
                fixture.guard_poll()
            progress_before = guard.guard_progress_payload(runtime_root=fixture.root)
            token_before = self._cycle_token(fixture.root)

            scratch = fixture.guard_paths.root / guard.CREATE_SCRATCH_DIRECTORY
            scratch.mkdir(mode=0o700, exist_ok=True)
            residue = scratch / (
                f"{fixture.guard_paths.intents.name}."
                f"{FIRST_TARGET.isoformat()}.json.deadbeef.tmp"
            )
            residue.write_bytes(b"sigkill-equivalent-incomplete-record")

            self.assertEqual(
                guard.guard_progress_payload(runtime_root=fixture.root),
                progress_before,
            )
            self.assertEqual(self._cycle_token(fixture.root), token_before)

            near_match = fixture.guard_paths.intents / (
                f".{FIRST_TARGET.isoformat()}.json.deadbeef.tm"
            )
            near_match.write_bytes(b"not-a-guard-record")
            with self.assertRaisesRegex(
                guard.VerifiedLiveIntegrityError, "directory contains pollution"
            ):
                guard.guard_progress_payload(runtime_root=fixture.root)

    def test_independent_runtime_record_times_do_not_change_cycle_token(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
            ):
                fixture.guard_poll()
            first_progress = guard.guard_progress_payload(runtime_root=fixture.root)
            first_token = self._cycle_token(fixture.root)

            with tempfile.TemporaryDirectory(
                prefix="verified-live-semantic-clone-"
            ) as raw:
                clone_root = Path(raw) / "runtime"
                shutil.copytree(fixture.root, clone_root)
                self._rebase_guard_chain_with_different_times(clone_root)
                clone_progress = guard.guard_progress_payload(
                    runtime_root=clone_root
                )
                clone_token = self._cycle_token(clone_root)

            self.assertEqual(clone_progress, first_progress)
            self.assertEqual(clone_token, first_token)
            rendered = json.dumps(first_progress, sort_keys=True)
            for forbidden in (
                "verified_at_utc",
                "created_at_utc",
                "completed_at_utc",
                '"size_bytes"',
                '"intent_sha256"',
                '"replay_receipt_sha256"',
                '"completion_sha256"',
            ):
                self.assertNotIn(forbidden, rendered)

    def test_failed_anchor_churn_before_issue_does_not_change_cycle_token(self) -> None:
        def guarded_poll(
            root: Path,
            *,
            now: datetime,
            anchor_client: live.AnchorClient | None = None,
        ) -> Path:
            with mock.patch.object(
                live_ledger, "_utc_now_text", return_value=guard._utc_text(now)
            ):
                return guard.poll_verified_live_runner(
                    runtime_root=root,
                    clock=lambda: now,
                    anchor_client=anchor_client,
                )

        def failed_anchor(_endpoint: str, _payload: dict[str, object]):
            raise RuntimeError("synthetic retryable anchor failure")

        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
            ):
                fixture.guard_poll()
            fixture.write_outcome()
            second_target = FIRST_TARGET + timedelta(days=1)
            fixture.write_issue(
                second_target,
                persistence={
                    station: value + 1.0
                    for station, value in fixture.latest.items()
                },
            )

            with tempfile.TemporaryDirectory(
                prefix="verified-live-anchor-churn-"
            ) as raw:
                clone_root = Path(raw) / "runtime"
                shutil.copytree(fixture.root, clone_root)
                self._rebase_guard_chain_with_different_times(clone_root)
                environment = {
                    "OOTANG_TIME_ANCHOR_URL": "https://anchor.invalid/v1"
                }
                with mock.patch.dict(os.environ, environment, clear=False):
                    guarded_poll(
                        fixture.root,
                        now=OUTCOME_CLOCK,
                        anchor_client=failed_anchor,
                    )
                    guarded_poll(
                        fixture.root,
                        now=OUTCOME_CLOCK,
                        anchor_client=failed_anchor,
                    )
                    guarded_poll(
                        clone_root,
                        now=OUTCOME_CLOCK,
                        anchor_client=failed_anchor,
                    )
                    guarded_poll(
                        fixture.root,
                        now=OUTCOME_CLOCK,
                        anchor_client=fixture.anchor_client(),
                    )
                    guarded_poll(
                        clone_root,
                        now=OUTCOME_CLOCK,
                        anchor_client=fixture.anchor_client(),
                    )
                    guarded_poll(fixture.root, now=OUTCOME_CLOCK)
                    guarded_poll(clone_root, now=OUTCOME_CLOCK)

                with mock.patch.object(
                    guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
                ):
                    guarded_poll(fixture.root, now=SECOND_ISSUE_CLOCK)

                original_input_snapshot = guard._input_snapshot

                def cloned_input_snapshot(issue: live.IssueBatch):
                    if issue.path.resolve().is_relative_to(clone_root.resolve()):
                        clone_path = (
                            clone_root / "artifacts" / issue.input_manifest.path.name
                        )
                        return guard._read_snapshot(
                            clone_path, name="cloned second issue input manifest"
                        )
                    return original_input_snapshot(issue)

                with (
                    mock.patch.object(
                        guard,
                        "_run_replay_under_lock",
                        side_effect=fixture.fake_replay,
                    ),
                    mock.patch.object(
                        guard,
                        "_input_snapshot",
                        side_effect=cloned_input_snapshot,
                    ),
                ):
                    guarded_poll(clone_root, now=SECOND_ISSUE_CLOCK)

                first_progress = guard.guard_progress_payload(
                    runtime_root=fixture.root
                )
                clone_progress = guard.guard_progress_payload(
                    runtime_root=clone_root
                )
                first_token = self._cycle_token(fixture.root)
                clone_token = self._cycle_token(clone_root)

            first_failures = sum(
                event.event_type == "anchor_failed" for event in fixture.events()
            )
            self.assertEqual(first_failures, 2)
            self.assertEqual(clone_progress, first_progress)
            self.assertEqual(clone_token, first_token)

    def test_orphan_intent_tamper_fails_progress_closed(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()

            def replay_then_outcome(*args: object, **kwargs: object):
                result = fixture.fake_replay(*args, **kwargs)
                fixture.write_outcome()
                return result

            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=replay_then_outcome
            ):
                fixture.guard_poll()
            intent_path = fixture.guard_paths.intents / f"{FIRST_TARGET}.json"
            self.assertTrue(intent_path.is_file())
            self.assertFalse(
                (fixture.guard_paths.completions / f"{FIRST_TARGET}.json").exists()
            )
            self.assertEqual(
                guard.guard_progress_payload(runtime_root=fixture.root)[
                    "pending_intent_targets"
                ],
                [FIRST_TARGET.isoformat()],
            )

            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            intent["formal_warning_output"] = True
            guard._atomic_write(intent_path, guard._canonical_bytes(intent))
            with self.assertRaisesRegex(
                guard.VerifiedLiveIntegrityError, "intent identity"
            ):
                guard.guard_progress_payload(runtime_root=fixture.root)

    def test_completion_time_is_progress_invariant_and_event_tamper_fails(self) -> None:
        with VerifiedLiveFixture() as fixture:
            fixture.install_prerequisites()
            fixture.guard_poll()
            fixture.write_issue()
            with mock.patch.object(
                guard, "_run_replay_under_lock", side_effect=fixture.fake_replay
            ):
                fixture.guard_poll()
            completion_path = (
                fixture.guard_paths.completions / f"{FIRST_TARGET}.json"
            )
            before = guard.guard_progress_payload(runtime_root=fixture.root)

            completion = json.loads(completion_path.read_text(encoding="utf-8"))
            completion["completed_at_utc"] = "2030-01-01T15:46:00.000000Z"
            guard._atomic_write(completion_path, guard._canonical_bytes(completion))
            after = guard.guard_progress_payload(runtime_root=fixture.root)
            self.assertEqual(after, before)

            events = guard._progress_live_events(fixture.guard_paths)
            changed_events = tuple(
                replace(
                    event,
                    payload={
                        **dict(event.payload),
                        "evidence_status": "changed_scientific_seal",
                    },
                )
                if event.event_type == "issue_batch_sealed"
                and event.target_date == FIRST_TARGET.isoformat()
                else event
                for event in events
            )
            with mock.patch.object(
                guard, "_progress_live_events", return_value=changed_events
            ):
                changed_science = guard.guard_progress_payload(
                    runtime_root=fixture.root
                )
            self.assertNotEqual(changed_science, before)
            self.assertNotEqual(
                changed_science["completions"][0][
                    "live_issue_seal_entry_science_sha256"
                ],
                before["completions"][0][
                    "live_issue_seal_entry_science_sha256"
                ],
            )

            completion["live_issue_seal_entry_sha256"] = "1" * 64
            guard._atomic_write(completion_path, guard._canonical_bytes(completion))
            with self.assertRaisesRegex(
                guard.VerifiedLiveIntegrityError, "verified live ledger events"
            ):
                guard.guard_progress_payload(runtime_root=fixture.root)


if __name__ == "__main__":
    unittest.main()
