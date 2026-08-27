"""Contracts for the machine-only R2b-2a drain eligibility observer."""

from __future__ import annotations

from contextlib import redirect_stderr
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain_eligibility as eligibility  # noqa: E402
from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402
from tests import test_ootang_epoch_drain as drain_tests  # noqa: E402


FIXED_NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)


class DrainEligibilityPublicTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-drain-eligibility-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.registry_root = self.base / "registry"
        self.active_root = self.base / "active"
        self.shadow_root = self.base / "shadow"

    def _observe(
        self, *, clock: eligibility.Clock | None = None
    ) -> eligibility.DrainEligibilityResult:
        return eligibility._observe_drain_eligibility(  # noqa: SLF001
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
            clock=clock or (lambda: FIXED_NOW),
        )

    def _synthetic_clean(self) -> drain._CleanState:  # noqa: SLF001
        semantic_raw = b'{"kind":"semantic"}\n'
        activation_raw = b'{"kind":"activation"}\n'
        source_root = self.active_root / "source"
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / "semantic.json").write_bytes(semantic_raw)
        (source_root / "activation.json").write_bytes(activation_raw)
        return drain._CleanState(  # noqa: SLF001
            old_live_epoch_id="a" * 64,
            live_event_count=1,
            live_terminal_sha256="b" * 64,
            guard_record_count=0,
            trusted_time_record_count=0,
            shadow_event_count=0,
            shadow_terminal_sha256=drain.ZERO_HASH,
            issue_route_inventory_sha256=hashlib.sha256(
                b'{"records":[]}\n'
            ).hexdigest(),
            issue_route_inventory=(),
            outcome_registry_inventory=(),
            guard_inventory=(),
            trusted_time_inventory=(),
            source_authority={
                "outcome_source_id": "source-one",
                "watermark": "2020-07-01",
                "exported_at_utc": "2020-07-01T08:00:00Z",
                "semantic_manifest": {
                    "path": "source/semantic.json",
                    "sha256": hashlib.sha256(semantic_raw).hexdigest(),
                    "size_bytes": len(semantic_raw),
                },
                "activation_manifest": {
                    "path": "source/activation.json",
                    "sha256": hashlib.sha256(activation_raw).hexdigest(),
                    "size_bytes": len(activation_raw),
                },
                "snapshot_receipt": None,
                "snapshot_sequence_id": 0,
                "records": [],
                "revision_ids_by_date": [],
            },
            live_entry_sha256s=("b" * 64,),
            shadow_entry_sha256s=(),
        )

    def _synthetic_assessment(
        self,
        current: drain._CleanState | drain._Waiting | None = None,  # noqa: SLF001
    ) -> eligibility._DrainingAssessment:  # noqa: SLF001
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        event_payload = {
            "entry_sha256": "e" * 64,
            "candidate_id": "f" * 64,
            "old_live_epoch_id": "a" * 64,
            "lifecycle_state": "DRAINING",
            "recorded_at_utc": eligibility._utc_text(  # noqa: SLF001
                FIXED_NOW - timedelta(seconds=10)
            ),
            "canonical_old_issue_route_fenced": True,
            "drain_boundary": {
                "path": "objects/sha256/boundary.json",
                "sha256": "1" * 64,
                "size_bytes": 2,
            },
            "exchange_attempt": {
                "path": "drain_exchange_attempts/attempt.json",
                "sha256": "2" * 64,
                "size_bytes": 3,
            },
            "fence_intent": {
                "path": "drain_intents/intent.json",
                "sha256": "3" * 64,
                "size_bytes": 4,
            },
        }
        raw = (
            json.dumps(event_payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        event_path = paths.drain.events / "00000000000000000001.json"
        event_path.parent.mkdir(parents=True)
        event_path.write_bytes(raw)
        snapshot = registry.ArtifactSnapshot(
            event_path,
            raw,
            hashlib.sha256(raw).hexdigest(),
            len(raw),
        )
        return eligibility._DrainingAssessment(  # noqa: SLF001
            authority=mock.sentinel.authority,
            event=event_payload,
            event_snapshot=snapshot,
            current=current or self._synthetic_clean(),
        )

    def _publish_synthetic(
        self, assessment: eligibility._DrainingAssessment | None = None
    ) -> tuple[eligibility.DrainEligibilityResult, eligibility._DrainingAssessment]:  # noqa: SLF001
        selected = assessment or self._synthetic_assessment()
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=selected
        ):
            return self._observe(), selected

    def test_absent_drain_authority_waits_without_eligibility_event(self) -> None:
        result = self._observe()

        self.assertEqual(result.status, "waiting_for_epoch_draining")
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        self.assertFalse(paths.events.exists())
        status = json.loads(result.status_path.read_bytes())
        self.assertEqual(status["lifecycle_state"], "PREPARING")
        self.assertFalse(status["drained_eligibility_current"])
        self.assertFalse(status["old_epoch_drained"])

    def test_in_progress_r2b_binding_without_event_waits(self) -> None:
        binding = ("7" * 64, "8" * 64)
        with (
            mock.patch.object(
                drain, "_persisted_authority_binding", return_value=binding
            ),
            mock.patch.object(
                drain, "_load_authority", return_value=mock.sentinel.authority
            ),
            mock.patch.object(drain, "replay_drain", return_value=()) as replay,
            mock.patch.object(drain, "_persisted_issue_archive") as archive,
        ):
            result = self._observe()

        self.assertEqual(result.status, "waiting_for_epoch_draining")
        self.assertEqual(replay.call_count, 1)
        archive.assert_not_called()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        self.assertFalse(paths.events.exists())
        status = json.loads(result.status_path.read_bytes())
        self.assertFalse(status["drained_eligibility_current"])
        self.assertEqual(status["lifecycle_state"], "PREPARING")

    def test_clean_draining_state_publishes_non_authoritative_observation(self) -> None:
        assessment = self._synthetic_assessment()

        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            result = self._observe()

        self.assertEqual(result.status, "drain_eligibility_observed")
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        events = list(paths.events.glob("*.json"))
        self.assertEqual(len(events), 1)
        event = json.loads(events[0].read_bytes())
        self.assertEqual(event["event_type"], "drain_eligibility_observed")
        self.assertEqual(event["lifecycle_state"], "DRAINING")
        self.assertTrue(event["drained_eligibility_current_at_publication"])
        self.assertTrue(event["observation_authority_only"])
        self.assertFalse(event["transition_authority"])
        for claim in eligibility.FALSE_CLAIMS:
            self.assertFalse(event[claim])
        observation_path = paths.root / event["observation"]["path"]
        observation = json.loads(observation_path.read_bytes())
        self.assertNotIn("captured_at_utc", observation)
        self.assertTrue(observation["observation_authority_only"])
        self.assertEqual(observation["clean_state"]["live_entry_sha256s"], ["b" * 64])
        for claim in eligibility.FALSE_CLAIMS:
            self.assertFalse(observation[claim])

    def test_exact_current_is_idempotent_and_settled_extension_appends(self) -> None:
        first, assessment = self._publish_synthetic()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        first_event_path = first.event_path
        self.assertIsNotNone(first_event_path)
        first_event_bytes = first_event_path.read_bytes()
        first_event = json.loads(first_event_bytes)
        first_observation = paths.root / first_event["observation"]["path"]
        first_observation_bytes = first_observation.read_bytes()

        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            idempotent = self._observe()

        self.assertEqual(idempotent.status, "drain_eligibility_current_idempotent")
        self.assertEqual(first_event_path.read_bytes(), first_event_bytes)
        self.assertEqual(first_observation.read_bytes(), first_observation_bytes)
        self.assertEqual(len(list(paths.events.iterdir())), 1)
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe(clock=lambda: FIXED_NOW - timedelta(seconds=1))

        clean = assessment.current
        self.assertIsInstance(clean, drain._CleanState)  # noqa: SLF001
        extended_clean = replace(
            clean,
            live_event_count=2,
            live_terminal_sha256="4" * 64,
            live_entry_sha256s=("b" * 64, "4" * 64),
        )
        extended = replace(assessment, current=extended_clean)
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=extended
        ):
            refreshed = self._observe()

        self.assertEqual(refreshed.status, "drain_eligibility_observation_refreshed")
        self.assertEqual(first_event_path.read_bytes(), first_event_bytes)
        events = sorted(paths.events.iterdir())
        self.assertEqual(len(events), 2)
        second_event = json.loads(events[-1].read_bytes())
        self.assertEqual(second_event["sequence_id"], 2)
        self.assertEqual(
            second_event["previous_entry_sha256"], first_event["entry_sha256"]
        )
        self.assertNotEqual(
            second_event["observation"]["sha256"],
            first_event["observation"]["sha256"],
        )

    def test_pending_after_observation_marks_current_false_without_event(self) -> None:
        _, assessment = self._publish_synthetic()
        pending = replace(
            assessment,
            current=drain._Waiting(  # noqa: SLF001
                "waiting_for_pending_guard", "old guard transaction is settling"
            ),
        )
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=pending
        ):
            result = self._observe()

        self.assertEqual(result.status, "waiting_for_clean_drain_eligibility")
        status = json.loads(result.status_path.read_bytes())
        self.assertEqual(status["lifecycle_state"], "DRAINING")
        self.assertFalse(status["drained_eligibility_current"])
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        self.assertEqual(len(list(paths.events.iterdir())), 1)

    def test_event_ahead_of_behind_status_repairs_cache_forward(self) -> None:
        _, assessment = self._publish_synthetic()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        first_status = paths.status.read_bytes()
        clean = assessment.current
        self.assertIsInstance(clean, drain._CleanState)  # noqa: SLF001
        extended = replace(
            assessment,
            current=replace(
                clean,
                live_event_count=2,
                live_terminal_sha256="7" * 64,
                live_entry_sha256s=("b" * 64, "7" * 64),
            ),
        )
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=extended
        ):
            refreshed = self._observe()
        self.assertEqual(refreshed.status, "drain_eligibility_observation_refreshed")

        paths.status.write_bytes(first_status)
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=extended
        ):
            recovered = self._observe()
        self.assertEqual(recovered.status, "drain_eligibility_current_idempotent")
        status = json.loads(paths.status.read_bytes())
        event = json.loads(refreshed.event_path.read_bytes())
        self.assertEqual(status["event_count"], 2)
        self.assertEqual(status["terminal_entry_sha256"], event["entry_sha256"])

    def test_capacity_wait_and_integrity_failure_clear_current_cache(self) -> None:
        _, assessment = self._publish_synthetic()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        clean = assessment.current
        self.assertIsInstance(clean, drain._CleanState)  # noqa: SLF001
        extended = replace(
            assessment,
            current=replace(
                clean,
                live_event_count=2,
                live_terminal_sha256="9" * 64,
                live_entry_sha256s=("b" * 64, "9" * 64),
            ),
        )
        small_profile = copy.deepcopy(profile)
        small_profile["protocol"]["maximum_observation_bytes"] = 1
        with (
            mock.patch.object(
                eligibility, "load_eligibility_profile", return_value=small_profile
            ),
            mock.patch.object(
                eligibility, "_load_draining_assessment", return_value=extended
            ),
        ):
            waiting = self._observe()

        self.assertEqual(waiting.status, "waiting_for_drain_eligibility_capacity")
        self.assertEqual(len(list(paths.events.iterdir())), 1)
        status = json.loads(paths.status.read_bytes())
        self.assertFalse(status["drained_eligibility_current"])
        self.assertFalse(status["cache_authority"])

        rolled_back = replace(
            assessment,
            current=replace(
                clean,
                live_terminal_sha256="6" * 64,
                live_entry_sha256s=("6" * 64,),
            ),
        )
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=rolled_back
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()
        blocked = json.loads(paths.status.read_bytes())
        self.assertEqual(blocked["eligibility_status"], "blocked_integrity")
        self.assertFalse(blocked["drained_eligibility_current"])
        self.assertFalse(blocked["cache_authority"])

    def test_crash_temp_cleanup_and_unknown_temp_fail_closed(self) -> None:
        assessment = self._synthetic_assessment()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        temporary = paths.root / ".tmp"
        temporary.mkdir(parents=True)
        leftover = temporary / ".interrupted.deadbeef.create"
        leftover.write_bytes(b"crash temporary\n")
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            self._observe()
        self.assertFalse(leftover.exists())

        self.registry_root = self.base / "unknown-temp-registry"
        assessment = self._synthetic_assessment()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        temporary = paths.root / ".tmp"
        temporary.mkdir(parents=True)
        (temporary / "unknown").write_bytes(b"tamper\n")
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

    def test_first_observation_cannot_predate_r2b_event(self) -> None:
        assessment = self._synthetic_assessment()
        future_event = dict(assessment.event)
        future_event["recorded_at_utc"] = eligibility._utc_text(  # noqa: SLF001
            FIXED_NOW + timedelta(seconds=1)
        )
        assessment = replace(assessment, event=future_event)
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        self.assertFalse(paths.events.exists())
        status = json.loads(paths.status.read_bytes())
        self.assertEqual(status["eligibility_status"], "blocked_integrity")
        self.assertFalse(status["drained_eligibility_current"])

        self.registry_root = self.base / "historical-clock-registry"
        result, assessment = self._publish_synthetic()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        event = json.loads(result.event_path.read_bytes())
        event["recorded_at_utc"] = eligibility._utc_text(  # noqa: SLF001
            FIXED_NOW - timedelta(seconds=20)
        )
        unsigned = {key: value for key, value in event.items() if key != "entry_sha256"}
        event["entry_sha256"] = eligibility._sha256(  # noqa: SLF001
            eligibility._canonical_bytes(unsigned)  # noqa: SLF001
        )
        replacement = eligibility._event_path(  # noqa: SLF001
            paths, 1, event["entry_sha256"]
        )
        result.event_path.unlink()
        replacement.write_bytes(eligibility._canonical_bytes(event))  # noqa: SLF001
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

    def test_final_capture_advance_leaves_only_orphan_cas_and_waits(self) -> None:
        first = self._synthetic_assessment()
        clean = first.current
        self.assertIsInstance(clean, drain._CleanState)  # noqa: SLF001
        advanced = replace(
            first,
            current=replace(
                clean,
                live_event_count=2,
                live_terminal_sha256="5" * 64,
                live_entry_sha256s=("b" * 64, "5" * 64),
            ),
        )
        with mock.patch.object(
            eligibility,
            "_load_draining_assessment",
            side_effect=[first, advanced],
        ):
            result = self._observe()

        self.assertEqual(result.status, "waiting_for_clean_drain_eligibility")
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        self.assertFalse(paths.events.exists())
        observations = list(paths.objects.glob(f"*{eligibility.OBSERVATION_SUFFIX}"))
        self.assertEqual(len(observations), 1)
        self.assertEqual(json.loads(result.status_path.read_bytes())["event_count"], 0)

    def test_final_capture_rollback_and_clock_rollback_fail_closed(self) -> None:
        first = self._synthetic_assessment()
        clean = first.current
        self.assertIsInstance(clean, drain._CleanState)  # noqa: SLF001
        replaced_clean = replace(
            clean,
            live_terminal_sha256="6" * 64,
            live_entry_sha256s=("6" * 64,),
        )
        with mock.patch.object(
            eligibility,
            "_load_draining_assessment",
            side_effect=[first, replace(first, current=replaced_clean)],
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

        second_root = self.base / "clock-registry"
        self.registry_root = second_root
        assessment = self._synthetic_assessment()
        samples = iter((FIXED_NOW, FIXED_NOW - timedelta(seconds=1)))
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe(clock=lambda: next(samples))

        self.registry_root = self.base / "authority-rollback-registry"
        assessment = self._synthetic_assessment()
        disappeared = drain._Waiting(  # noqa: SLF001
            "waiting_for_epoch_draining", "R2b event disappeared"
        )
        with mock.patch.object(
            eligibility,
            "_load_draining_assessment",
            side_effect=[assessment, disappeared],
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

    def test_event_suffix_rollback_is_detected_by_head(self) -> None:
        result, assessment = self._publish_synthetic()
        self.assertIsNotNone(result.event_path)
        result.event_path.unlink()

        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

    def test_blocked_status_preserves_deleted_suffix_witness(self) -> None:
        _, assessment = self._publish_synthetic()
        clean = assessment.current
        self.assertIsInstance(clean, drain._CleanState)  # noqa: SLF001
        extended = replace(
            assessment,
            current=replace(
                clean,
                live_event_count=2,
                live_terminal_sha256="4" * 64,
                live_entry_sha256s=("b" * 64, "4" * 64),
            ),
        )
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=extended
        ):
            refreshed = self._observe()
        self.assertEqual(refreshed.status, "drain_eligibility_observation_refreshed")

        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        original_tip = json.loads(refreshed.event_path.read_bytes())["entry_sha256"]
        paths.head.unlink()
        refreshed.event_path.unlink()

        for _ in range(2):
            with mock.patch.object(
                eligibility, "_load_draining_assessment", return_value=extended
            ):
                with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                    self._observe()
            status = json.loads(paths.status.read_bytes())
            self.assertEqual(status["eligibility_status"], "blocked_integrity")
            self.assertEqual(status["event_count"], 2)
            self.assertEqual(status["terminal_entry_sha256"], original_tip)
            self.assertFalse(status["drained_eligibility_current"])
            self.assertEqual(len(list(paths.events.iterdir())), 1)

    def test_same_count_forged_status_tip_is_not_promoted_to_witness(self) -> None:
        _, assessment = self._publish_synthetic()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        event = json.loads(next(paths.events.iterdir()).read_bytes())
        status = json.loads(paths.status.read_bytes())
        status["terminal_entry_sha256"] = "9" * 64
        paths.status.write_bytes(eligibility._canonical_bytes(status))  # noqa: SLF001

        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()
        blocked = json.loads(paths.status.read_bytes())
        self.assertEqual(blocked["event_count"], 1)
        self.assertEqual(blocked["terminal_entry_sha256"], event["entry_sha256"])
        self.assertEqual(blocked["eligibility_status"], "blocked_integrity")
        self.assertFalse(blocked["drained_eligibility_current"])

        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            recovered = self._observe()
        self.assertEqual(recovered.status, "drain_eligibility_current_idempotent")

        status = json.loads(paths.status.read_bytes())
        status["eligibility_status"] = "drain_eligibility_observed"
        status["event_count"] = 2
        status["terminal_entry_sha256"] = "8" * 64
        paths.status.write_bytes(eligibility._canonical_bytes(status))  # noqa: SLF001
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()
        blocked = json.loads(paths.status.read_bytes())
        self.assertEqual(blocked["event_count"], 1)
        self.assertEqual(blocked["terminal_entry_sha256"], event["entry_sha256"])

    def test_early_upstream_failure_preserves_status_suffix_witness(self) -> None:
        result, _ = self._publish_synthetic()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        tip = json.loads(result.event_path.read_bytes())["entry_sha256"]
        paths.head.unlink()
        result.event_path.unlink()

        for _ in range(2):
            with mock.patch.object(
                eligibility,
                "_load_draining_assessment",
                side_effect=eligibility.DrainEligibilityIntegrityError(
                    "upstream replay failed"
                ),
            ):
                with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                    self._observe()
            blocked = json.loads(paths.status.read_bytes())
            self.assertEqual(blocked["lifecycle_state"], "DRAINING")
            self.assertEqual(blocked["event_count"], 1)
            self.assertEqual(blocked["terminal_entry_sha256"], tip)
            self.assertFalse(blocked["drained_eligibility_current"])

    def test_historical_observation_rechecks_frozen_source_objects(self) -> None:
        _, assessment = self._publish_synthetic()
        semantic = self.active_root / "source" / "semantic.json"
        semantic.write_bytes(b'{"kind":"tampered"}\n')

        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        blocked = json.loads(paths.status.read_bytes())
        self.assertEqual(blocked["eligibility_status"], "blocked_integrity")
        self.assertFalse(blocked["drained_eligibility_current"])

    def test_event_observation_and_cache_tamper_fail_closed(self) -> None:
        result, assessment = self._publish_synthetic()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        event = json.loads(result.event_path.read_bytes())
        observation = paths.root / event["observation"]["path"]
        observation.write_bytes(observation.read_bytes() + b" ")
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

        observation.write_bytes(
            eligibility._canonical_bytes(  # noqa: SLF001
                json.loads(observation.read_bytes().rstrip())
            )
        )
        event_bytes = result.event_path.read_bytes()
        result.event_path.write_bytes(event_bytes + b" ")
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()
        result.event_path.write_bytes(event_bytes)

        status = json.loads(paths.status.read_bytes())
        status["unexpected"] = False
        paths.status.write_bytes(eligibility._canonical_bytes(status))  # noqa: SLF001
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

    def test_head_loss_repairs_forward_but_head_ahead_blocks(self) -> None:
        _, assessment = self._publish_synthetic()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        paths.head.unlink()
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            result = self._observe()
        self.assertEqual(result.status, "drain_eligibility_current_idempotent")
        self.assertTrue(paths.head.is_file())

        head = json.loads(paths.head.read_bytes())
        head["sequence_id"] = 2
        paths.head.write_bytes(eligibility._canonical_bytes(head))  # noqa: SLF001
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

    def test_branch_symlink_busy_and_public_surface_fail_closed(self) -> None:
        assessment = self._synthetic_assessment()
        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        paths.events.mkdir(parents=True)
        (paths.events / "unexpected.json").write_text("{}\n")
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()

        (paths.events / "unexpected.json").unlink()
        paths.events.rmdir()
        target = self.base / "event-target"
        target.mkdir()
        paths.events.symlink_to(target, target_is_directory=True)
        with mock.patch.object(
            eligibility, "_load_draining_assessment", return_value=assessment
        ):
            with self.assertRaises(eligibility.DrainEligibilityIntegrityError):
                self._observe()
        paths.events.unlink()

        registry._mkdir(paths.root, root=paths.root)  # noqa: SLF001
        handle = drain._acquire_lock(paths.manager_lock, label="manager")  # noqa: SLF001
        try:
            with self.assertRaises(eligibility.DrainEligibilityBusyError):
                self._observe()
        finally:
            drain._release_locks([handle])  # noqa: SLF001

        signature = inspect.signature(eligibility.observe_drain_eligibility)
        self.assertEqual(list(signature.parameters), ["config_path"])
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                eligibility._parse_args(["--force"])  # noqa: SLF001
        with (
            mock.patch.object(
                eligibility,
                "observe_drain_eligibility",
                side_effect=eligibility.DrainEligibilityBusyError("busy"),
            ),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(eligibility.main([]), 3)
        with (
            mock.patch.object(
                eligibility,
                "observe_drain_eligibility",
                side_effect=eligibility.DrainEligibilityIntegrityError("blocked"),
            ),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(eligibility.main([]), 2)
        self.assertEqual(
            profile["upstream"]["drain_profile"]["expected_sha256"],
            drain.DEFAULT_CONFIG_SHA256,
        )
        with mock.patch.object(eligibility, "DEFAULT_CONFIG_SHA256", "0" * 64):
            with self.assertRaises(eligibility.DrainEligibilityConfigError):
                eligibility.load_eligibility_profile()

    def test_public_path_uses_historical_binding_and_full_r2b_replay(self) -> None:
        assessment = self._synthetic_assessment()
        archive = self.base / "archived-old-issue-route"
        archive.mkdir()
        binding = ("7" * 64, "8" * 64)
        with (
            mock.patch.object(
                drain, "_persisted_authority_binding", return_value=binding
            ) as persisted,
            mock.patch.object(
                drain, "_load_authority", return_value=assessment.authority
            ) as load_authority,
            mock.patch.object(
                drain, "replay_drain", return_value=(assessment.event,)
            ) as replay,
            mock.patch.object(
                drain, "_load_single_intent", return_value=mock.sentinel.intent
            ) as load_intent,
            mock.patch.object(
                drain,
                "_verify_intent",
                return_value=(
                    {"archive_route": str(archive)},
                    mock.sentinel.capsule,
                    assessment.current,
                ),
            ) as verify_intent,
            mock.patch.object(
                drain,
                "_verify_boundary",
                return_value=(mock.sentinel.boundary, assessment.current),
            ) as verify_boundary,
            mock.patch.object(
                drain, "_persisted_issue_archive", return_value=archive
            ) as persisted_archive,
            mock.patch.object(
                drain, "_verify_draining_runtime_prefix"
            ) as verify_prefix,
            mock.patch.object(
                drain, "_load_clean_state", return_value=assessment.current
            ) as load_clean,
        ):
            result = self._observe()

        self.assertEqual(result.status, "drain_eligibility_observed")
        self.assertEqual(persisted.call_count, 2)
        self.assertEqual(load_authority.call_count, 2)
        self.assertEqual(load_intent.call_count, 2)
        self.assertEqual(verify_intent.call_count, 2)
        self.assertEqual(verify_boundary.call_count, 2)
        self.assertEqual(persisted_archive.call_count, 2)
        self.assertEqual(verify_prefix.call_count, 2)
        self.assertEqual(load_clean.call_count, 2)
        self.assertEqual(replay.call_count, 4)
        for call in load_authority.call_args_list:
            self.assertEqual(call.kwargs["registry_entry_sha256"], binding[0])
            self.assertEqual(call.kwargs["preparation_entry_sha256"], binding[1])
        for call in load_clean.call_args_list:
            self.assertEqual(call.kwargs["archived_issue_route"], archive)
            self.assertTrue(call.kwargs["allow_queued_incoming"])
        for call in verify_intent.call_args_list:
            self.assertFalse(call.kwargs["require_current_implementation"])
        for call in verify_prefix.call_args_list:
            self.assertEqual(call.args[2], archive)

        event = json.loads(result.event_path.read_bytes())
        status = json.loads(result.status_path.read_bytes())
        for claim in eligibility.FALSE_CLAIMS:
            self.assertFalse(event[claim])
            self.assertFalse(status[claim])


class DrainEligibilityR2bIntegrationTests(unittest.TestCase):
    def test_real_r2b_authority_observes_then_repolls_idempotently(self) -> None:
        helper = drain_tests.EpochDrainIntegrationTests(
            methodName="test_crash_recovery_fence_tamper_and_idempotent_authority"
        )
        helper.profile = drain.load_drain_profile()
        helper.r1_profile = registry.load_registry_profile()
        helper.preparation_profile = drain_tests.preparation.load_preparation_profile()
        helper.setUp()
        self.addCleanup(helper.doCleanups)

        drain_result = helper._run()
        self.assertEqual(drain_result.status, "epoch_draining")
        drain_paths = helper._paths()

        def frozen_r2b_bytes() -> dict[str, bytes]:
            captured: dict[str, bytes] = {}
            for namespace in (
                drain_paths.events,
                drain_paths.intents,
                drain_paths.fence_prepares,
                drain_paths.exchange_attempts,
                drain_paths.capsules,
            ):
                for path in sorted(namespace.iterdir()):
                    if path.is_file():
                        captured[path.relative_to(drain_paths.root).as_posix()] = (
                            path.read_bytes()
                        )
            for cache in (drain_paths.head, drain_paths.status):
                captured[cache.relative_to(drain_paths.root).as_posix()] = (
                    cache.read_bytes()
                )
            marker = drain_paths.issue_inbox / drain.ARMED_ATTEMPT_MARKER_NAME
            captured["canonical-issue-inbox/armed-attempt-marker"] = marker.read_bytes()
            return captured

        before = frozen_r2b_bytes()
        first = eligibility._observe_drain_eligibility(  # noqa: SLF001
            runtime_root=helper.registry_root,
            active_runtime_root=helper.active_root,
            shadow_runtime_root=helper.shadow_root,
            clock=lambda: FIXED_NOW,
        )
        self.assertEqual(first.status, "drain_eligibility_observed")
        second = eligibility._observe_drain_eligibility(  # noqa: SLF001
            runtime_root=helper.registry_root,
            active_runtime_root=helper.active_root,
            shadow_runtime_root=helper.shadow_root,
            clock=lambda: FIXED_NOW,
        )
        self.assertEqual(second.status, "drain_eligibility_current_idempotent")
        self.assertEqual(before, frozen_r2b_bytes())

        profile = eligibility.load_eligibility_profile()
        paths = eligibility.eligibility_paths(
            profile,
            runtime_root=helper.registry_root,
            active_runtime_root=helper.active_root,
            shadow_runtime_root=helper.shadow_root,
        )
        events = sorted(paths.events.iterdir())
        self.assertEqual(len(events), 1)
        event = json.loads(events[0].read_bytes())
        self.assertTrue(event["drained_eligibility_current_at_publication"])
        for claim in eligibility.FALSE_CLAIMS:
            self.assertFalse(event[claim])


if __name__ == "__main__":
    unittest.main()
