"""Contracts for the replay-gated Ootang fixed-point cycle v3."""

from __future__ import annotations

import os
import hashlib
import json
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_cycle_v3 as cycle  # noqa: E402


class CycleV3ContractTests(unittest.TestCase):
    def test_default_profile_binds_all_three_reviewed_profiles(self) -> None:
        self.assertEqual(
            hashlib.sha256(cycle.DEFAULT_CONFIG_PATH.read_bytes()).hexdigest(),
            cycle.DEFAULT_CONFIG_SHA256,
        )
        profile = cycle.load_cycle_v3_profile()
        self.assertEqual(profile["_profile_sha256"], cycle.DEFAULT_CONFIG_SHA256)
        self.assertEqual(
            profile["issue_replay_profile"]["expected_sha256"],
            cycle.ISSUE_REPLAY_PROFILE_SHA256,
        )
        self.assertEqual(
            profile["verified_live_profile"]["expected_sha256"],
            cycle.VERIFIED_LIVE_PROFILE_SHA256,
        )

    def test_live_and_shadow_runtime_roots_remain_independent(self) -> None:
        profile = cycle.load_cycle_v3_profile()
        with tempfile.TemporaryDirectory(prefix="cycle-v3-live-") as live_raw:
            with tempfile.TemporaryDirectory(prefix="cycle-v3-shadow-") as shadow_raw:
                live = Path(live_raw)
                shadow = Path(shadow_raw)
                paths = cycle.runtime_paths_v3(
                    profile,
                    runtime_root=live,
                    shadow_runtime_root=shadow,
                )
                self.assertEqual(paths.root, live.resolve())
                self.assertEqual(paths.shadow_root, shadow.resolve())
                self.assertEqual(paths.cycle_lock, paths.base.cycle_lock)
                self.assertEqual(
                    paths.replay_status,
                    live.resolve() / "issue_replay_status.json",
                )
                self.assertEqual(
                    paths.verified_live_status,
                    live.resolve() / "verified_live_status.json",
                )

    def test_stage_order_places_replay_before_recovery_and_issue_seal(self) -> None:
        self.assertEqual(
            cycle.EXPECTED_STAGE_ORDER,
            (
                "issue_replay_before_source",
                "shadow_before_source",
                "source_ingest",
                "bundle_ensure",
                "verified_live_reconcile_before_outcome",
                "shadow_after_live_before_outcome",
                "outcome_materialize",
                "verified_live_reconcile_after_outcome",
                "shadow_after_outcome",
                "issue_produce",
                "issue_replay_after_issue",
                "verified_live_seal_issue",
                "shadow_after_issue",
            ),
        )

    def test_designated_outer_lock_rejects_final_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cycle-v3-lock-") as raw:
            root = Path(raw)
            target = root / "attacker-target.lock"
            target.write_bytes(b"untouched")
            link = root / "cycle.lock"
            link.symlink_to(target)

            with self.assertRaises(cycle.CycleV3IntegrityError):
                cycle._acquire_cycle_lock(link)

            self.assertEqual(target.read_bytes(), b"untouched")

    def test_bound_stage_status_rejects_final_symlink(self) -> None:
        profile = cycle.load_cycle_v3_profile()
        with tempfile.TemporaryDirectory(prefix="cycle-v3-status-link-") as raw:
            paths = cycle.runtime_paths_v3(
                profile,
                runtime_root=Path(raw) / "live",
                shadow_runtime_root=Path(raw) / "shadow",
            )
            paths.root.mkdir(parents=True)
            target = paths.root / "replay.real.json"
            target.write_text(
                json.dumps(
                    {
                        "schema_version": cycle.REPLAY_STATUS_SCHEMA,
                        "profile_id": cycle.REPLAY_PROFILE_ID,
                        "artifact_status": profile["_replay_artifact_status"],
                        "profile_sha256": cycle.ISSUE_REPLAY_PROFILE_SHA256,
                        "runtime_root": str(paths.root.resolve()),
                        "replay_status": "waiting_for_source_model_or_issue",
                        "runner_independent_checkpoint_inference_replayed": False,
                        "input_manifest_semantics_verified": False,
                        "outcome_read": False,
                        "automatic_calibration_promotion": False,
                    }
                ),
                encoding="utf-8",
            )
            paths.replay_status.symlink_to(target)

            with self.assertRaises(cycle.CycleV3IntegrityError):
                cycle._read_bound_stage_status(
                    paths.replay_status,
                    expected_path=paths.replay_status,
                    profile=profile,
                    paths=paths,
                    kind="issue replay",
                )

    def test_continuation_status_rejects_final_symlink(self) -> None:
        profile = cycle.load_cycle_v3_profile()
        with tempfile.TemporaryDirectory(prefix="cycle-v3-cont-link-") as raw:
            paths = cycle.runtime_paths_v3(
                profile,
                runtime_root=Path(raw) / "live",
                shadow_runtime_root=Path(raw) / "shadow",
            )
            paths.root.mkdir(parents=True)
            target = paths.root / "cycle.real.json"
            target.write_text(
                json.dumps({"cycle_status": "converged_waiting"}),
                encoding="utf-8",
            )
            paths.cycle_status.symlink_to(target)

            with self.assertRaises(cycle.CycleV3IntegrityError):
                cycle._load_continuation_history(profile, paths)

    def test_bound_statuses_reject_key_drift_and_false_guard_claims(self) -> None:
        profile = cycle.load_cycle_v3_profile()
        with tempfile.TemporaryDirectory(prefix="cycle-v3-status-contract-") as raw:
            root = Path(raw)
            cycle.run_cycle_v3(
                runtime_root=root / "live",
                shadow_runtime_root=root / "shadow",
            )
            paths = cycle.runtime_paths_v3(
                profile,
                runtime_root=root / "live",
                shadow_runtime_root=root / "shadow",
            )
            replay_payload = json.loads(
                paths.replay_status.read_text(encoding="utf-8")
            )
            replay_payload["target_date"] = "2030-01-02"
            paths.replay_status.write_text(
                json.dumps(replay_payload), encoding="utf-8"
            )
            waiting = cycle._read_bound_stage_status(
                paths.replay_status,
                expected_path=paths.replay_status,
                profile=profile,
                paths=paths,
                kind="issue replay",
            )
            self.assertEqual(
                waiting.status, "waiting_for_source_model_or_issue"
            )

            replay_payload["unexpected"] = False
            paths.replay_status.write_text(
                json.dumps(replay_payload), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                cycle.CycleV3IntegrityError, "status keys changed"
            ):
                cycle._read_bound_stage_status(
                    paths.replay_status,
                    expected_path=paths.replay_status,
                    profile=profile,
                    paths=paths,
                    kind="issue replay",
                )

            guard_payload = json.loads(
                paths.verified_live_status.read_text(encoding="utf-8")
            )
            guard_payload["runner_independent_checkpoint_inference_replayed"] = True
            paths.verified_live_status.write_text(
                json.dumps(guard_payload), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                cycle.CycleV3IntegrityError, "claim is inconsistent"
            ):
                cycle._read_bound_stage_status(
                    paths.verified_live_status,
                    expected_path=paths.verified_live_status,
                    profile=profile,
                    paths=paths,
                    kind="verified live",
                )


class CycleV3ProgressTests(unittest.TestCase):
    def test_progress_binds_v2_replay_and_guard_scientific_projections(self) -> None:
        profile = cycle.load_cycle_v3_profile()
        with tempfile.TemporaryDirectory(prefix="cycle-v3-progress-") as raw:
            paths = cycle.runtime_paths_v3(
                profile,
                runtime_root=Path(raw) / "live",
                shadow_runtime_root=Path(raw) / "shadow",
            )
            payload = cycle.progress_token_payload_v3(
                paths,
                profile,
                v2_token_builder=lambda _paths, _profile: "a" * 64,
                replay_payload_builder=lambda **_kwargs: {
                    "verified_receipts": ["b" * 64]
                },
                guard_payload_builder=lambda **_kwargs: {"completed_links": ["c" * 64]},
            )
        self.assertEqual(payload["base_cycle_v2_progress_sha256"], "a" * 64)
        self.assertEqual(
            payload["verified_issue_replay_projection"],
            {"verified_receipts": ["b" * 64]},
        )
        self.assertEqual(
            payload["verified_live_guard_projection"],
            {"completed_links": ["c" * 64]},
        )
        rendered = json.dumps(payload).lower()
        self.assertNotIn("recorded_at", rendered)
        self.assertNotIn("status", rendered)

    def test_progress_fence_rejects_base_or_replay_changes(self) -> None:
        profile = cycle.load_cycle_v3_profile()
        with tempfile.TemporaryDirectory(prefix="cycle-v3-fence-") as raw:
            paths = cycle.runtime_paths_v3(
                profile,
                runtime_root=Path(raw) / "live",
                shadow_runtime_root=Path(raw) / "shadow",
            )
            base_values = iter(("a" * 64, "b" * 64))
            with self.assertRaises(cycle.CycleV3BusyError):
                cycle.progress_token_payload_v3(
                    paths,
                    profile,
                    v2_token_builder=lambda _paths, _profile: next(base_values),
                    replay_payload_builder=lambda **_kwargs: {"cursor": 1},
                    guard_payload_builder=lambda **_kwargs: {"cursor": 1},
                )
            replay_values = iter(({"cursor": 1}, {"cursor": 2}))
            with self.assertRaises(cycle.CycleV3BusyError):
                cycle.progress_token_payload_v3(
                    paths,
                    profile,
                    v2_token_builder=lambda _paths, _profile: "a" * 64,
                    replay_payload_builder=lambda **_kwargs: next(replay_values),
                    guard_payload_builder=lambda **_kwargs: {"cursor": 1},
                )


def _waiting_dependencies(order: list[str] | None = None) -> cycle.CycleV3Dependencies:
    def stage(name: str, status: str):
        def call() -> str:
            if order is not None:
                order.append(name)
            return status

        return call

    return cycle.CycleV3Dependencies(
        source_ingest=stage("source_ingest", "waiting_for_daily_finalized_feed"),
        bundle_ensure=stage(
            "bundle_ensure", "waiting_for_semantically_validated_source"
        ),
        outcome_materialize=stage(
            "outcome_materialize", "waiting_for_source_model_or_ledger"
        ),
        issue_produce=stage("issue_produce", "waiting_for_source_or_model"),
        shadow_reconcile=stage("shadow", "waiting_for_live_prerequisites"),
        issue_replay=stage("issue_replay", "waiting_for_source_model_or_issue"),
        verified_live_reconcile=stage(
            "verified_live", "waiting_for_live_prerequisites"
        ),
    )


class CycleV3FixedPointTests(unittest.TestCase):
    def _run(self, root: Path, **kwargs):
        with mock.patch.dict(os.environ, {cycle.TEST_OVERRIDE_ENV: "1"}):
            return cycle.run_cycle_v3(
                runtime_root=root / "live",
                shadow_runtime_root=root / "shadow",
                clock=lambda: datetime(2026, 8, 26, tzinfo=timezone.utc),
                **kwargs,
            )

    def test_one_stable_iteration_runs_exact_thirteen_stage_order(self) -> None:
        order: list[str] = []
        with tempfile.TemporaryDirectory(prefix="cycle-v3-fixed-") as raw:
            result = self._run(
                Path(raw),
                dependencies=_waiting_dependencies(order),
                progress_token_builder=lambda: "a" * 64,
            )
            payload = json.loads(result.status_path.read_text(encoding="utf-8"))
        self.assertEqual(result.status, "converged_waiting")
        self.assertEqual(result.iterations, 1)
        self.assertEqual(
            [record["stage"] for record in payload["iteration_records"][0]["stages"]],
            list(cycle.EXPECTED_STAGE_ORDER),
        )
        self.assertEqual(
            order,
            [
                "issue_replay",
                "shadow",
                "source_ingest",
                "bundle_ensure",
                "verified_live",
                "shadow",
                "outcome_materialize",
                "verified_live",
                "shadow",
                "issue_produce",
                "issue_replay",
                "verified_live",
                "shadow",
            ],
        )
        self.assertTrue(payload["designated_entrypoint_replay_gate_implemented"])
        self.assertFalse(payload["e2_live_evidence_eligible"])

    def test_real_empty_runtime_uses_public_adapters_and_converges_waiting(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="cycle-v3-production-") as raw:
            root = Path(raw)
            result = cycle.run_cycle_v3(
                runtime_root=root / "live",
                shadow_runtime_root=root / "shadow",
            )
            payload = json.loads(result.status_path.read_text(encoding="utf-8"))
        self.assertEqual(result.status, "converged_waiting")
        self.assertEqual(
            [record["stage"] for record in payload["iteration_records"][0]["stages"]],
            list(cycle.EXPECTED_STAGE_ORDER),
        )
        self.assertTrue(
            all(
                record["status_path"]
                for record in payload["iteration_records"][0]["stages"]
            )
        )

    def test_work_remaining_without_scientific_change_fails_closed(self) -> None:
        dependencies = _waiting_dependencies()
        dependencies = cycle.CycleV3Dependencies(
            source_ingest=dependencies.source_ingest,
            bundle_ensure=dependencies.bundle_ensure,
            outcome_materialize=dependencies.outcome_materialize,
            issue_produce=dependencies.issue_produce,
            shadow_reconcile=dependencies.shadow_reconcile,
            issue_replay=dependencies.issue_replay,
            verified_live_reconcile=lambda: "work_remaining",
        )
        with tempfile.TemporaryDirectory(prefix="cycle-v3-stale-") as raw:
            root = Path(raw)
            with self.assertRaises(cycle.CycleV3IntegrityError):
                self._run(
                    root,
                    dependencies=dependencies,
                    progress_token_builder=lambda: "a" * 64,
                )
            payload = json.loads(
                (root / "live" / "cycle_v3_status.json").read_text(encoding="utf-8")
            )
        self.assertEqual(payload["cycle_status"], "blocked_integrity")
        self.assertIn("work_remaining", payload["reason"])

    def test_bounded_unique_progress_returns_machine_continuation(self) -> None:
        counter = iter(f"{value:064x}" for value in range(1, 130))
        with tempfile.TemporaryDirectory(prefix="cycle-v3-bounded-") as raw:
            result = self._run(
                Path(raw),
                dependencies=_waiting_dependencies(),
                progress_token_builder=lambda: next(counter),
            )
            payload = json.loads(result.status_path.read_text(encoding="utf-8"))
        self.assertEqual(result.status, "work_remaining")
        self.assertEqual(result.iterations, 64)
        self.assertEqual(len(payload["continuation_token_history"]), 128)
        self.assertEqual(
            payload["continuation_token_history"][-1],
            result.progress_token_sha256,
        )

    def test_oscillation_and_stage_crash_publish_blocked_state(self) -> None:
        oscillating = iter(("a" * 64, "b" * 64, "a" * 64))
        with tempfile.TemporaryDirectory(prefix="cycle-v3-oscillation-") as raw:
            root = Path(raw)
            with self.assertRaises(cycle.CycleV3IntegrityError):
                self._run(
                    root,
                    dependencies=_waiting_dependencies(),
                    progress_token_builder=lambda: next(oscillating),
                )
            status = json.loads((root / "live" / "cycle_v3_status.json").read_text())
        self.assertEqual(status["cycle_status"], "blocked_integrity")
        self.assertIn("oscillated", status["reason"])

        dependencies = _waiting_dependencies()
        crashed = cycle.CycleV3Dependencies(
            source_ingest=dependencies.source_ingest,
            bundle_ensure=lambda: (_ for _ in ()).throw(RuntimeError("crash")),
            outcome_materialize=dependencies.outcome_materialize,
            issue_produce=dependencies.issue_produce,
            shadow_reconcile=dependencies.shadow_reconcile,
            issue_replay=dependencies.issue_replay,
            verified_live_reconcile=dependencies.verified_live_reconcile,
        )
        with tempfile.TemporaryDirectory(prefix="cycle-v3-crash-") as raw:
            root = Path(raw)
            with self.assertRaises(cycle.CycleV3IntegrityError):
                self._run(
                    root,
                    dependencies=crashed,
                    progress_token_builder=lambda: "c" * 64,
                )
            status = json.loads((root / "live" / "cycle_v3_status.json").read_text())
        self.assertIn("bundle_ensure", status["reason"])

    def test_shared_outer_lock_reports_busy_without_running_stages(self) -> None:
        profile = cycle.load_cycle_v3_profile()
        calls: list[str] = []
        with tempfile.TemporaryDirectory(prefix="cycle-v3-lock-") as raw:
            root = Path(raw)
            paths = cycle.runtime_paths_v3(
                profile,
                runtime_root=root / "live",
                shadow_runtime_root=root / "shadow",
            )
            paths.cycle_lock.parent.mkdir(parents=True, exist_ok=True)
            with paths.cycle_lock.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(cycle.CycleV3BusyError):
                    self._run(
                        root,
                        dependencies=_waiting_dependencies(calls),
                        progress_token_builder=lambda: "a" * 64,
                    )
        self.assertEqual(calls, [])

    def test_injection_requires_explicit_environment_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cycle-v3-gate-") as raw:
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(cycle.CycleV3ConfigError):
                    cycle.run_cycle_v3(
                        runtime_root=Path(raw) / "live",
                        shadow_runtime_root=Path(raw) / "shadow",
                        dependencies=_waiting_dependencies(),
                    )


if __name__ == "__main__":
    unittest.main()
