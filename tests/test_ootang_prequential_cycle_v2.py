"""Contracts for the additive Ootang calibration-shadow fixed-point cycle."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_cycle as cycle_v1  # noqa: E402
from monitoring import ootang_prequential_cycle_v2 as cycle  # noqa: E402


NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


class _Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ootang-cycle-v2-test-", dir=ROOT
        )
        self.directory = Path(self.temporary.name)
        self.runtime = self.directory / "runtime"
        self.shadow_runtime = self.directory / "shadow-runtime"
        self.shadow_profile_path = self.directory / "shadow.json"
        self.config_path = self.directory / "cycle-v2.json"
        self.shadow_profile = {
            "schema_version": cycle.SHADOW_PROFILE_SCHEMA,
            "profile_id": cycle.SHADOW_PROFILE_ID,
            "profile_version": "1.0.0-engineering",
            "case": "ootang",
            "artifact_status": (
                "e2_calibration_shadow_engineering_only_not_live_evidence"
            ),
            "formal_warning_output": False,
            "independent_label_used": False,
            "confirmatory_external_validation": False,
            "vajont_used": False,
            "default_pipeline_member": False,
            "runtime": {
                "root": self.shadow_runtime.relative_to(ROOT).as_posix(),
                "ledger": "ledger.sqlite3",
                "status": "status.json",
                "lock": "runner.lock",
            },
        }
        _write_json(self.shadow_profile_path, self.shadow_profile)
        shadow_sha = hashlib.sha256(self.shadow_profile_path.read_bytes()).hexdigest()
        self.profile = {
            "schema_version": "ootang_prequential_cycle_profile_v2",
            "profile_id": "ootang-prequential-cycle-v2",
            "profile_version": "2.0.0-engineering",
            "case": "ootang",
            "artifact_status": (
                "e2_calibration_shadow_engineering_only_not_live_evidence"
            ),
            "formal_warning_output": False,
            "independent_label_used": False,
            "confirmatory_external_validation": False,
            "vajont_used": False,
            "default_pipeline_member": False,
            "base_cycle_profile": {
                "path": "config/ootang_prequential_cycle.v1.json",
                "expected_sha256": cycle.BASE_CYCLE_PROFILE_SHA256,
            },
            "calibration_shadow_profile": {
                "path": self.shadow_profile_path.relative_to(ROOT).as_posix(),
                "expected_sha256": shadow_sha,
            },
            "runtime": {"cycle_status": "cycle_v2_status.json"},
            "cycle": {
                "stage_order": list(cycle.EXPECTED_STAGE_ORDER),
                "max_iterations": 64,
                "stable_iterations_to_stop": 1,
                "progress_token_schema_version": (
                    "ootang_prequential_cycle_progress_v2"
                ),
                "busy_exit_code": 3,
                "blocked_exit_code": 2,
            },
            "engineering_capabilities": dict(cycle.EXPECTED_CAPABILITIES),
        }
        _write_json(self.config_path, self.profile)
        self.loaded = cycle.load_cycle_v2_profile(self.config_path)
        self.paths = cycle.runtime_paths_v2(
            self.loaded,
            runtime_root=self.runtime,
            shadow_runtime_root=self.shadow_runtime,
        )
        self.previous_override = os.environ.get(cycle.TEST_OVERRIDE_ENV)
        os.environ[cycle.TEST_OVERRIDE_ENV] = "1"

    def close(self) -> None:
        if self.previous_override is None:
            os.environ.pop(cycle.TEST_OVERRIDE_ENV, None)
        else:
            os.environ[cycle.TEST_OVERRIDE_ENV] = self.previous_override
        self.temporary.cleanup()

    def __enter__(self) -> _Fixture:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def waiting_dependencies(
        calls: list[str] | None = None,
    ) -> cycle.CycleV2Dependencies:
        observed = calls if calls is not None else []

        def stage(name: str, status: str):
            def invoke() -> cycle_v1.StageOutcome:
                observed.append(name)
                return cycle_v1.StageOutcome(status)

            return invoke

        live_calls = 0
        shadow_calls = 0

        def live() -> cycle_v1.StageOutcome:
            nonlocal live_calls
            names = (
                "live_reconcile_before_outcome",
                "live_reconcile_after_outcome",
                "live_seal_issue",
            )
            observed.append(names[live_calls % 3])
            live_calls += 1
            return cycle_v1.StageOutcome(
                "waiting_for_production_bundle_or_source_snapshot"
            )

        def shadow() -> cycle_v1.StageOutcome:
            nonlocal shadow_calls
            names = (
                "shadow_before_source",
                "shadow_after_live_before_outcome",
                "shadow_after_outcome",
                "shadow_after_issue",
            )
            observed.append(names[shadow_calls % 4])
            shadow_calls += 1
            return cycle_v1.StageOutcome("waiting_for_live_prerequisites")

        return cycle.CycleV2Dependencies(
            source_ingest=stage("source_ingest", "waiting_for_daily_finalized_feed"),
            bundle_ensure=stage(
                "bundle_ensure", "waiting_for_semantically_validated_source"
            ),
            live_reconcile=live,
            outcome_materialize=stage(
                "outcome_materialize", "waiting_for_source_model_or_ledger"
            ),
            issue_produce=stage("issue_produce", "waiting_for_source_or_model"),
            shadow_reconcile=shadow,
        )


class CycleV2ProfileTests(unittest.TestCase):
    def test_default_profile_bytes_are_exactly_reviewed(self) -> None:
        self.assertEqual(
            hashlib.sha256(cycle.DEFAULT_CONFIG_PATH.read_bytes()).hexdigest(),
            cycle.DEFAULT_CONFIG_SHA256,
        )
        profile = cycle.load_cycle_v2_profile()
        self.assertEqual(profile["_profile_sha256"], cycle.DEFAULT_CONFIG_SHA256)

    def test_deep_profile_json_is_a_normalized_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deep.json"
            path.write_text('{"child":' * 2_000 + "0" + "}" * 2_000)
            with self.assertRaises(cycle.CycleV2ConfigError):
                cycle.load_cycle_v2_profile(path)

    def test_only_default_path_requires_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="cycle-v2-default-sha-", dir=ROOT
        ) as raw:
            directory = Path(raw)
            exact_copy = directory / "default.json"
            exact_copy.write_bytes(cycle.DEFAULT_CONFIG_PATH.read_bytes())
            with mock.patch.object(cycle, "DEFAULT_CONFIG_PATH", exact_copy):
                cycle.load_cycle_v2_profile(exact_copy)
                exact_copy.write_bytes(exact_copy.read_bytes() + b"\n")
                with self.assertRaisesRegex(cycle.CycleV2ConfigError, "reviewed file"):
                    cycle.load_cycle_v2_profile(exact_copy)

            custom_copy = directory / "custom.json"
            custom_copy.write_bytes(cycle.DEFAULT_CONFIG_PATH.read_bytes() + b"\n")
            custom = cycle.load_cycle_v2_profile(custom_copy)
            self.assertNotEqual(custom["_profile_sha256"], cycle.DEFAULT_CONFIG_SHA256)

    def test_profile_binds_unchanged_base_v1_and_exact_shadow_bytes(self) -> None:
        with _Fixture() as fixture:
            self.assertEqual(
                fixture.loaded["_base_profile_payload"]["profile_id"],
                "ootang-prequential-cycle-v1",
            )
            self.assertEqual(
                fixture.loaded["_shadow_profile_sha256"],
                fixture.profile["calibration_shadow_profile"]["expected_sha256"],
            )
            self.assertEqual(fixture.paths.cycle_lock, fixture.paths.base.cycle_lock)
            self.assertNotEqual(
                fixture.paths.cycle_status, fixture.paths.base.cycle_status
            )
            self.assertEqual(
                fixture.paths.shadow_root, fixture.shadow_runtime.resolve()
            )
            self.assertNotEqual(fixture.paths.root, fixture.paths.shadow_root)

    def test_live_and_shadow_runtime_overrides_are_independent(self) -> None:
        with _Fixture() as fixture:
            alternate_live = fixture.directory / "alternate-live"
            alternate_shadow = fixture.directory / "alternate-shadow"
            paths = cycle.runtime_paths_v2(
                fixture.loaded,
                runtime_root=alternate_live,
                shadow_runtime_root=alternate_shadow,
            )
            self.assertEqual(paths.root, alternate_live.resolve())
            self.assertEqual(paths.shadow_root, alternate_shadow.resolve())
            self.assertEqual(paths.cycle_lock, paths.base.cycle_lock)
            self.assertEqual(paths.shadow_status, alternate_shadow / "status.json")

    def test_shadow_profile_mutation_breaks_the_exact_binding(self) -> None:
        with _Fixture() as fixture:
            fixture.shadow_profile["profile_version"] = "changed"
            _write_json(fixture.shadow_profile_path, fixture.shadow_profile)
            with self.assertRaisesRegex(cycle.CycleV2ConfigError, "SHA-256 changed"):
                cycle.load_cycle_v2_profile(fixture.config_path)

    def test_stage_order_and_fixed_cycle_values_are_not_configurable(self) -> None:
        with _Fixture() as fixture:
            for field, replacement in (
                ("stage_order", list(reversed(cycle.EXPECTED_STAGE_ORDER))),
                ("max_iterations", 65),
                ("busy_exit_code", 0),
            ):
                with self.subTest(field=field):
                    changed = json.loads(json.dumps(fixture.profile))
                    changed["cycle"][field] = replacement
                    _write_json(fixture.config_path, changed)
                    with self.assertRaises(cycle.CycleV2ConfigError):
                        cycle.load_cycle_v2_profile(fixture.config_path)


class CycleV2ProgressTokenTests(unittest.TestCase):
    def test_production_shadow_snapshot_receives_both_runtime_roots(self) -> None:
        with _Fixture() as fixture:
            shadow_payload = {"projection_state_sha256": "b" * 64}
            with mock.patch(
                "monitoring.ootang_prequential_calibration_shadow."
                "shadow_progress_token_payload",
                return_value=shadow_payload,
            ) as snapshot:
                payload = cycle.progress_token_payload_v2(
                    fixture.paths,
                    fixture.loaded,
                    base_token_builder=lambda _paths: "a" * 64,
                )
            snapshot.assert_called_once_with(
                config_path=fixture.paths.shadow_config,
                runtime_root=fixture.paths.shadow_root,
                live_runtime_root=fixture.paths.root,
                project_root=ROOT,
            )
            self.assertEqual(
                payload["verified_calibration_shadow_projection"],
                shadow_payload,
            )

    def test_combined_token_binds_base_and_shadow_but_not_status(self) -> None:
        with _Fixture() as fixture:
            base = "a" * 64
            shadow = {"cursor": 10, "state_sha256": "b" * 64}
            payload = cycle.progress_token_payload_v2(
                fixture.paths,
                fixture.loaded,
                base_token_builder=lambda _paths: base,
                shadow_payload_builder=lambda **_kwargs: shadow,
            )
            self.assertEqual(payload["base_cycle_progress_sha256"], base)
            self.assertEqual(payload["verified_calibration_shadow_projection"], shadow)
            self.assertNotIn("status", json.dumps(payload).lower())
            self.assertNotIn("recorded_at", json.dumps(payload).lower())

    def test_base_change_across_shadow_snapshot_is_busy_not_a_torn_token(self) -> None:
        with _Fixture() as fixture:
            values = iter(("a" * 64, "b" * 64))
            with self.assertRaisesRegex(cycle.CycleV2BusyError, "changed across"):
                cycle.build_progress_token_v2(
                    fixture.paths,
                    fixture.loaded,
                    base_token_builder=lambda _paths: next(values),
                    shadow_payload_builder=lambda **_kwargs: {"cursor": 1},
                )

    def test_nonfinite_shadow_payload_is_blocked(self) -> None:
        with _Fixture() as fixture:
            with self.assertRaises(cycle.CycleV2IntegrityError):
                cycle.build_progress_token_v2(
                    fixture.paths,
                    fixture.loaded,
                    base_token_builder=lambda _paths: "a" * 64,
                    shadow_payload_builder=lambda **_kwargs: {"bad": float("nan")},
                )

    def test_deep_shadow_payload_error_is_normalized(self) -> None:
        with _Fixture() as fixture:
            payload: dict[str, object] = {}
            cursor = payload
            for _ in range(2_000):
                child: dict[str, object] = {}
                cursor["child"] = child
                cursor = child
            with self.assertRaisesRegex(
                cycle.CycleV2IntegrityError, "canonical finite JSON"
            ):
                cycle.build_progress_token_v2(
                    fixture.paths,
                    fixture.loaded,
                    base_token_builder=lambda _paths: "a" * 64,
                    shadow_payload_builder=lambda **_kwargs: payload,
                )


class CycleV2FixedPointTests(unittest.TestCase):
    def test_empty_input_converges_in_exact_eleven_stage_order(self) -> None:
        with _Fixture() as fixture:
            calls: list[str] = []
            result = cycle.run_cycle_v2(
                fixture.config_path,
                runtime_root=fixture.runtime,
                clock=lambda: NOW,
                dependencies=fixture.waiting_dependencies(calls),
                progress_token_builder=lambda: "a" * 64,
            )
            self.assertEqual(result.status, "converged_waiting")
            self.assertEqual(result.iterations, 1)
            self.assertEqual(calls, list(cycle.EXPECTED_STAGE_ORDER))
            status = json.loads(result.status_path.read_text())
            self.assertEqual(
                status["schema_version"], "ootang_prequential_cycle_status_v2"
            )
            self.assertFalse(status["promotion_performed"])
            self.assertFalse(status["e2_live_evidence_eligible"])

    def test_work_remaining_with_stable_token_blocks_instead_of_converging(
        self,
    ) -> None:
        with _Fixture() as fixture:
            dependencies = fixture.waiting_dependencies()
            contradictory = cycle.CycleV2Dependencies(
                source_ingest=dependencies.source_ingest,
                bundle_ensure=dependencies.bundle_ensure,
                live_reconcile=dependencies.live_reconcile,
                outcome_materialize=dependencies.outcome_materialize,
                issue_produce=dependencies.issue_produce,
                shadow_reconcile=lambda: cycle_v1.StageOutcome("work_remaining"),
            )
            with self.assertRaisesRegex(
                cycle.CycleV2IntegrityError,
                "work_remaining without changing",
            ):
                cycle.run_cycle_v2(
                    fixture.config_path,
                    runtime_root=fixture.runtime,
                    clock=lambda: NOW,
                    dependencies=contradictory,
                    progress_token_builder=lambda: "a" * 64,
                )
            status = json.loads(fixture.paths.cycle_status.read_text())
            self.assertEqual(status["cycle_status"], "blocked_integrity")

    def test_test_injection_requires_the_explicit_v2_environment_gate(self) -> None:
        with _Fixture() as fixture:
            previous = os.environ.pop(cycle.TEST_OVERRIDE_ENV, None)
            try:
                with self.assertRaisesRegex(
                    cycle.CycleV2ConfigError, "explicit test mode"
                ):
                    cycle.run_cycle_v2(
                        fixture.config_path,
                        runtime_root=fixture.runtime,
                        dependencies=fixture.waiting_dependencies(),
                    )
            finally:
                if previous is not None:
                    os.environ[cycle.TEST_OVERRIDE_ENV] = previous

    def test_unknown_shadow_status_is_blocked_and_persisted(self) -> None:
        with _Fixture() as fixture:
            dependencies = fixture.waiting_dependencies()
            hostile = cycle.CycleV2Dependencies(
                source_ingest=dependencies.source_ingest,
                bundle_ensure=dependencies.bundle_ensure,
                live_reconcile=dependencies.live_reconcile,
                outcome_materialize=dependencies.outcome_materialize,
                issue_produce=dependencies.issue_produce,
                shadow_reconcile=lambda: cycle_v1.StageOutcome("promoted"),
            )
            with self.assertRaisesRegex(
                cycle.CycleV2IntegrityError, "unknown or fatal"
            ):
                cycle.run_cycle_v2(
                    fixture.config_path,
                    runtime_root=fixture.runtime,
                    clock=lambda: NOW,
                    dependencies=hostile,
                    progress_token_builder=lambda: "a" * 64,
                )
            status = json.loads(fixture.paths.cycle_status.read_text())
            self.assertEqual(status["cycle_status"], "blocked_integrity")
            self.assertFalse(status["automatic_calibration_promotion_implemented"])

    def test_production_dependencies_keep_outcome_on_base_cycle_v1_config(self) -> None:
        with _Fixture() as fixture:
            base = fixture.waiting_dependencies()
            captured: dict[str, object] = {}

            def build_base(profile, *, config_path, paths, project_root):
                captured.update(
                    {
                        "profile": profile,
                        "config_path": config_path,
                        "paths": paths,
                        "project_root": project_root,
                    }
                )
                return cycle_v1.CycleDependencies(
                    source_ingest=base.source_ingest,
                    bundle_ensure=base.bundle_ensure,
                    live_reconcile=base.live_reconcile,
                    outcome_materialize=base.outcome_materialize,
                    issue_produce=base.issue_produce,
                )

            with mock.patch.object(
                cycle_v1, "_production_dependencies", side_effect=build_base
            ):
                dependencies = cycle._production_dependencies(
                    fixture.loaded, fixture.paths
                )
            self.assertEqual(captured["config_path"], fixture.paths.base_config)
            self.assertEqual(
                fixture.paths.base_config,
                ROOT / "config" / "ootang_prequential_cycle.v1.json",
            )
            self.assertIs(dependencies.outcome_materialize, base.outcome_materialize)
            with mock.patch(
                "monitoring.ootang_prequential_calibration_shadow."
                "reconcile_calibration_shadow",
                return_value=mock.sentinel.shadow_result,
            ) as reconcile:
                result = dependencies.shadow_reconcile()
            self.assertIs(result, mock.sentinel.shadow_result)
            reconcile.assert_called_once_with(
                config_path=fixture.paths.shadow_config,
                runtime_root=fixture.paths.shadow_root,
                live_runtime_root=fixture.paths.root,
                project_root=ROOT,
            )

    def test_shadow_durable_status_path_provenance_and_allowlist_are_strict(
        self,
    ) -> None:
        with _Fixture() as fixture:
            payload = {
                "schema_version": cycle.SHADOW_STATUS_SCHEMA,
                "profile_id": cycle.SHADOW_PROFILE_ID,
                "profile_sha256": fixture.loaded["_shadow_profile_sha256"],
                "artifact_status": fixture.loaded["_shadow_artifact_status"],
                "runtime_root": str(fixture.paths.shadow_root),
                "runner_status": "waiting_for_live_issue",
                "formal_warning_output": False,
                "promotion_performed": False,
                "e2_live_evidence_eligible": False,
                "real_activation_ready": False,
            }
            _write_json(fixture.paths.shadow_status, payload)
            outcome = cycle._shadow_stage_outcome(
                fixture.paths.shadow_status,
                profile=fixture.loaded,
                paths=fixture.paths,
                require_status_path=True,
            )
            self.assertEqual(outcome.status, "waiting_for_live_issue")

            payload["runner_status"] = "work_remaining"
            _write_json(fixture.paths.shadow_status, payload)
            outcome = cycle._shadow_stage_outcome(
                fixture.paths.shadow_status,
                profile=fixture.loaded,
                paths=fixture.paths,
                require_status_path=True,
            )
            self.assertEqual(outcome.status, "work_remaining")

            payload["runner_status"] = "promoted"
            _write_json(fixture.paths.shadow_status, payload)
            with self.assertRaisesRegex(
                cycle.CycleV2IntegrityError, "unknown or fatal"
            ):
                cycle._shadow_stage_outcome(
                    fixture.paths.shadow_status,
                    profile=fixture.loaded,
                    paths=fixture.paths,
                    require_status_path=True,
                )

    def test_shared_v1_cycle_lock_is_nonblocking_and_returns_busy(self) -> None:
        with _Fixture() as fixture:
            fixture.paths.cycle_lock.parent.mkdir(parents=True, exist_ok=True)
            with fixture.paths.cycle_lock.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(cycle.CycleV2BusyError):
                    cycle.run_cycle_v2(
                        fixture.config_path,
                        runtime_root=fixture.runtime,
                        clock=lambda: NOW,
                        dependencies=fixture.waiting_dependencies(),
                        progress_token_builder=lambda: "a" * 64,
                    )
            self.assertFalse(fixture.paths.cycle_status.exists())

    def test_cycle_lock_oserror_is_normalized(self) -> None:
        with _Fixture() as fixture:
            with mock.patch.object(
                cycle.fcntl,
                "flock",
                side_effect=OSError("synthetic lock failure"),
            ):
                with self.assertRaisesRegex(
                    cycle.CycleV2IntegrityError,
                    "cannot acquire the shared cycle lock",
                ):
                    cycle.run_cycle_v2(
                        fixture.config_path,
                        runtime_root=fixture.runtime,
                        clock=lambda: NOW,
                        dependencies=fixture.waiting_dependencies(),
                        progress_token_builder=lambda: "a" * 64,
                    )
            self.assertFalse(fixture.paths.cycle_status.exists())

    def test_busy_substage_maps_to_busy_status(self) -> None:
        with _Fixture() as fixture:
            dependencies = fixture.waiting_dependencies()

            class CalibrationShadowBusy(RuntimeError):
                pass

            hostile = cycle.CycleV2Dependencies(
                source_ingest=dependencies.source_ingest,
                bundle_ensure=dependencies.bundle_ensure,
                live_reconcile=dependencies.live_reconcile,
                outcome_materialize=dependencies.outcome_materialize,
                issue_produce=dependencies.issue_produce,
                shadow_reconcile=lambda: (_ for _ in ()).throw(
                    CalibrationShadowBusy("owned")
                ),
            )
            with self.assertRaises(cycle.CycleV2BusyError):
                cycle.run_cycle_v2(
                    fixture.config_path,
                    runtime_root=fixture.runtime,
                    clock=lambda: NOW,
                    dependencies=hostile,
                    progress_token_builder=lambda: "a" * 64,
                )
            status = json.loads(fixture.paths.cycle_status.read_text())
            self.assertEqual(status["cycle_status"], "busy_substage")

    def test_token_oscillation_is_blocked(self) -> None:
        with _Fixture() as fixture:
            sequence = iter(("a" * 64, "b" * 64, "b" * 64, "a" * 64))
            with self.assertRaisesRegex(cycle.CycleV2IntegrityError, "oscillated"):
                cycle.run_cycle_v2(
                    fixture.config_path,
                    runtime_root=fixture.runtime,
                    clock=lambda: NOW,
                    dependencies=fixture.waiting_dependencies(),
                    progress_token_builder=lambda: next(sequence),
                )
            status = json.loads(fixture.paths.cycle_status.read_text())
            self.assertEqual(status["cycle_status"], "blocked_integrity")

    def test_monotonic_backlog_returns_work_remaining_with_continuation(self) -> None:
        with _Fixture() as fixture:
            state = 0
            waiting = fixture.waiting_dependencies()

            def advance() -> cycle_v1.StageOutcome:
                nonlocal state
                state += 1
                return cycle_v1.StageOutcome("ready")

            dependencies = cycle.CycleV2Dependencies(
                source_ingest=advance,
                bundle_ensure=waiting.bundle_ensure,
                live_reconcile=waiting.live_reconcile,
                outcome_materialize=waiting.outcome_materialize,
                issue_produce=waiting.issue_produce,
                shadow_reconcile=waiting.shadow_reconcile,
            )
            result = cycle.run_cycle_v2(
                fixture.config_path,
                runtime_root=fixture.runtime,
                shadow_runtime_root=fixture.shadow_runtime,
                clock=lambda: NOW,
                dependencies=dependencies,
                progress_token_builder=lambda: _digest(state),
            )
            self.assertEqual(result.status, "work_remaining")
            self.assertEqual(result.iterations, 64)
            status = json.loads(result.status_path.read_text())
            self.assertEqual(len(status["continuation_token_history"]), 65)
            self.assertEqual(
                status["continuation_token_history"][-1], result.progress_token_sha256
            )


if __name__ == "__main__":
    unittest.main()
