import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("pipeline_main", ROOT / "main.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load main.py")
pipeline = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pipeline
SPEC.loader.exec_module(pipeline)


class PipelineTests(unittest.TestCase):
    def test_default_selection_is_current_minimal_chain(self):
        stages = pipeline.select_stages()

        self.assertEqual(len(pipeline.STAGES), 19)
        self.assertEqual(
            [stage.name for stage in stages],
            ["features", "convlstm", "ootang-operational-v4"],
        )
        self.assertTrue(all(stage.enabled_by_default for stage in stages))
        self.assertTrue(
            all(
                not stage.enabled_by_default
                for stage in pipeline.STAGES
                if stage not in stages
            )
        )

    def test_selected_stages_are_deduplicated_and_canonically_ordered(self):
        stages = pipeline.select_stages(["ngboost-shap", "features", "ngboost-shap"])

        self.assertEqual([stage.name for stage in stages], ["features", "ngboost-shap"])

    def test_v4_is_the_only_registered_operational_stage(self):
        self.assertNotIn("ootang-operational", pipeline.STAGE_BY_NAME)
        self.assertNotIn("ootang-operational-v2", pipeline.STAGE_BY_NAME)
        self.assertNotIn("ootang-operational-v3", pipeline.STAGE_BY_NAME)
        self.assertTrue(
            pipeline.STAGE_BY_NAME["ootang-operational-v4"].enabled_by_default
        )
        self.assertEqual(
            pipeline.STAGE_BY_NAME["ootang-operational-v4"].script,
            "code/warning/operational_run_v4.py",
        )
        self.assertIn(
            "config/ootang_warning_protocol.v2.draft.json",
            pipeline.STAGE_BY_NAME["ootang-operational-v4"].inputs,
        )
        operational = [
            stage.name for stage in pipeline.STAGES if "operational" in stage.name
        ]
        self.assertEqual(operational, ["ootang-operational-v4"])

    def test_ngboost_interval_proxy_pilot_is_explicit_and_isolated(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-ngboost-interval-proxy-pilot"]

        self.assertFalse(stage.enabled_by_default)
        self.assertEqual(
            names.index(stage.name),
            names.index("ootang-operational-v4") + 1,
        )
        self.assertEqual(
            stage.script,
            "code/warning/ootang_ngboost_interval_proxy_pilot.py",
        )
        self.assertIn(
            "config/ootang_ngboost_interval_proxy_pilot.v1.json",
            stage.inputs,
        )
        self.assertIn(
            "figures/warning_operational_draft_v4/ootang_operational_run_manifest.json",
            stage.inputs,
        )
        self.assertTrue(
            all(
                "vajont" not in path.lower() for path in (*stage.inputs, *stage.outputs)
            )
        )
        protected_outputs = {
            path
            for protected in pipeline.STAGES
            if protected.name in {"convlstm", "ngboost-shap", "ootang-operational-v4"}
            for path in protected.outputs
        }
        self.assertTrue(set(stage.outputs).isdisjoint(protected_outputs))
        self.assertNotIn("models/ngboost.pkl", stage.outputs)
        self.assertEqual(
            pipeline.STAGE_BY_NAME["ngboost-shap"].script,
            "code/explainability/ngboost_shap.py",
        )

    def test_ngboost_horizon_sensitivity_is_explicit_nonranking_and_isolated(self):
        names = [stage.name for stage in pipeline.STAGES]
        pilot = pipeline.STAGE_BY_NAME["ootang-ngboost-interval-proxy-pilot"]
        stage = pipeline.STAGE_BY_NAME[
            "ootang-ngboost-interval-proxy-horizon-sensitivity"
        ]

        self.assertFalse(stage.enabled_by_default)
        self.assertEqual(names.index(stage.name), names.index(pilot.name) + 1)
        self.assertEqual(
            stage.script,
            "code/warning/ootang_ngboost_interval_proxy_horizon_sensitivity.py",
        )
        self.assertEqual(
            stage.warning_artifact_scope,
            "exploratory_proxy_horizon_sensitivity",
        )
        self.assertIn(
            "config/ootang_ngboost_interval_proxy_horizon_sensitivity.v1.json",
            stage.inputs,
        )
        self.assertIn(
            "figures/ngboost_interval_proxy_pilot_ootang_v1/manifest.json",
            stage.inputs,
        )
        self.assertTrue(
            all(
                "vajont" not in path.lower() for path in (*stage.inputs, *stage.outputs)
            )
        )
        existing_outputs = {
            path
            for existing in pipeline.STAGES
            if existing.name != stage.name
            for path in existing.outputs
        }
        self.assertTrue(set(stage.outputs).isdisjoint(existing_outputs))
        self.assertEqual(
            sum(path.startswith("models/") for path in stage.outputs),
            3,
        )

    def test_ngboost_feature_ablation_is_explicit_nonranking_and_isolated(self):
        names = [stage.name for stage in pipeline.STAGES]
        sensitivity_stage = pipeline.STAGE_BY_NAME[
            "ootang-ngboost-interval-proxy-horizon-sensitivity"
        ]
        stage = pipeline.STAGE_BY_NAME["ootang-ngboost-interval-proxy-feature-ablation"]

        self.assertFalse(stage.enabled_by_default)
        self.assertEqual(
            names.index(stage.name),
            names.index(sensitivity_stage.name) + 1,
        )
        self.assertEqual(
            stage.script,
            "code/warning/ootang_ngboost_interval_proxy_feature_ablation.py",
        )
        self.assertEqual(
            stage.warning_artifact_scope,
            "exploratory_proxy_feature_ablation",
        )
        self.assertIn(
            "config/ootang_ngboost_interval_proxy_feature_ablation.v1.json",
            stage.inputs,
        )
        self.assertIn(
            "config/ootang_ngboost_interval_proxy_pilot.v1.json",
            stage.inputs,
        )
        self.assertIn(
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/manifest.json",
            stage.inputs,
        )
        self.assertTrue(
            all(
                "vajont" not in path.lower() for path in (*stage.inputs, *stage.outputs)
            )
        )
        existing_outputs = {
            path
            for existing in pipeline.STAGES
            if existing.name != stage.name
            for path in existing.outputs
        }
        self.assertTrue(set(stage.outputs).isdisjoint(existing_outputs))
        self.assertFalse(any(path.startswith("models/") for path in stage.outputs))

    def test_auto_v0_stage_is_explicit_nonformal_and_isolated(self):
        names = [stage.name for stage in pipeline.STAGES]
        ablation = pipeline.STAGE_BY_NAME[
            "ootang-ngboost-interval-proxy-feature-ablation"
        ]
        stage = pipeline.STAGE_BY_NAME["ootang-auto-v0-direct-bai-perron"]

        self.assertFalse(stage.enabled_by_default)
        self.assertEqual(names.index(stage.name), names.index(ablation.name) + 1)
        self.assertEqual(stage.script, "code/warning/auto_v0_direct_bai_perron.py")
        self.assertEqual(stage.warning_artifact_scope, "exploratory_v0_candidate")
        self.assertIn("config/ootang_auto_v0_direct_bai_perron.v1.json", stage.inputs)
        self.assertIn("data/ootang_kinematics_long.csv", stage.inputs)
        self.assertTrue(
            all(
                "vajont" not in path.lower() for path in (*stage.inputs, *stage.outputs)
            )
        )
        existing_outputs = {
            path
            for existing in pipeline.STAGES
            if existing.name != stage.name
            for path in existing.outputs
        }
        self.assertTrue(set(stage.outputs).isdisjoint(existing_outputs))
        self.assertFalse(any(path.startswith("models/") for path in stage.outputs))

    def test_v5_candidate_display_stage_is_explicit_nonformal_and_isolated(self):
        names = [stage.name for stage in pipeline.STAGES]
        auto_v0 = pipeline.STAGE_BY_NAME["ootang-auto-v0-direct-bai-perron"]
        stage = pipeline.STAGE_BY_NAME["ootang-v5-candidate-display"]

        self.assertFalse(stage.enabled_by_default)
        self.assertEqual(names.index(stage.name), names.index(auto_v0.name) + 1)
        self.assertEqual(
            stage.script,
            "code/warning/ootang_v5_candidate_display.py",
        )
        self.assertEqual(
            stage.warning_artifact_scope,
            "exploratory_v5_candidate_display",
        )
        self.assertIn("config/ootang_v5_candidate_display.v1.json", stage.inputs)
        self.assertIn(
            "figures/auto_v0_direct_bai_perron_ootang_v1/manifest.json",
            stage.inputs,
        )
        self.assertIn(
            "figures/warning_operational_draft_v4/ootang_operational_station_timeline.csv",
            stage.inputs,
        )
        self.assertIn(
            "figures/warning_operational_draft_v4/ootang_operational_site_timeline.csv",
            stage.inputs,
        )
        self.assertTrue(
            all(
                "vajont" not in path.lower() for path in (*stage.inputs, *stage.outputs)
            )
        )
        existing_outputs = {
            path
            for existing in pipeline.STAGES
            if existing.name != stage.name
            for path in existing.outputs
        }
        self.assertTrue(set(stage.outputs).isdisjoint(existing_outputs))
        self.assertTrue(
            all(
                path.startswith("figures/v5_candidate_display_ootang_v1/")
                for path in stage.outputs
            )
        )

    def test_ngboost_auto_state_stage_is_explicit_labels_only_and_isolated(self):
        stage = pipeline.STAGE_BY_NAME["ootang-ngboost-auto-state"]

        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "exploratory_auto_future_state_proxy",
        )
        self.assertEqual(stage.script, "code/warning/ootang_ngboost_auto_state.py")
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_ngboost_auto_state.v1.json"),
        )
        self.assertIn("config/ootang_ngboost_auto_state.v1.json", stage.inputs)
        self.assertTrue(
            all(
                path.startswith("figures/ngboost_auto_state_v1/")
                for path in stage.outputs
            )
        )
        self.assertFalse(any(path.startswith("models/") for path in stage.outputs))
        self.assertTrue(
            all(
                "vajont" not in path.lower() for path in (*stage.inputs, *stage.outputs)
            )
        )
        existing_outputs = {
            path
            for existing in pipeline.STAGES
            if existing.name != stage.name
            for path in existing.outputs
        }
        self.assertTrue(set(stage.outputs).isdisjoint(existing_outputs))

    def test_ngboost_auto_state_ecdf_stage_is_explicit_and_isolated(self):
        stage = pipeline.STAGE_BY_NAME["ootang-ngboost-auto-state-ecdf"]

        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "exploratory_auto_future_state_ecdf_proxy",
        )
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_ngboost_auto_state_ecdf.v2.json"),
        )
        self.assertIn(
            "figures/ngboost_auto_state_v1/station_auto_labels.csv", stage.inputs
        )
        self.assertTrue(
            all(
                path.startswith("figures/ngboost_auto_state_ecdf_v2/")
                for path in stage.outputs
            )
        )
        existing_outputs = {
            path
            for existing in pipeline.STAGES
            if existing.name != stage.name
            for path in existing.outputs
        }
        self.assertTrue(set(stage.outputs).isdisjoint(existing_outputs))

    def test_ngboost_auto_state_classifier_stage_is_explicit_and_isolated(self):
        stage = pipeline.STAGE_BY_NAME["ootang-ngboost-auto-state-classifier"]

        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "exploratory_auto_future_state_ngboost_classifier",
        )
        self.assertIn(
            "figures/ngboost_auto_state_ecdf_v2/label_gate.json", stage.inputs
        )
        self.assertIn(
            "figures/ngboost_auto_state_classifier_v1/site_predictions.csv",
            stage.outputs,
        )
        self.assertIn("models/ootang_ngboost_auto_state_site_v1.pkl", stage.outputs)
        existing_outputs = {
            path
            for existing in pipeline.STAGES
            if existing.name != stage.name
            for path in existing.outputs
        }
        self.assertTrue(set(stage.outputs).isdisjoint(existing_outputs))

    def test_ngboost_auto_state_memory_stage_is_explicit_and_isolated(self):
        names = [stage.name for stage in pipeline.STAGES]
        classifier_stage = pipeline.STAGE_BY_NAME[
            "ootang-ngboost-auto-state-classifier"
        ]
        stage = pipeline.STAGE_BY_NAME["ootang-ngboost-auto-state-memory"]

        self.assertEqual(
            names.index(stage.name), names.index(classifier_stage.name) + 1
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "exploratory_auto_future_state_lag7_memory_challenger",
        )
        self.assertIn(
            "figures/ngboost_auto_state_classifier_v1/site_predictions.csv",
            stage.inputs,
        )
        self.assertIn(
            "figures/ngboost_auto_state_memory_v2/site_predictions.csv",
            stage.outputs,
        )
        existing_outputs = {
            path
            for existing in pipeline.STAGES
            if existing.name != stage.name
            for path in existing.outputs
        }
        self.assertTrue(set(stage.outputs).isdisjoint(existing_outputs))

    def test_ngboost_auto_state_residual_stage_is_explicit_and_isolated(self):
        names = [stage.name for stage in pipeline.STAGES]
        memory_stage = pipeline.STAGE_BY_NAME["ootang-ngboost-auto-state-memory"]
        stage = pipeline.STAGE_BY_NAME["ootang-ngboost-auto-state-residual"]

        self.assertEqual(names.index(stage.name), names.index(memory_stage.name) + 1)
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "exploratory_auto_future_state_residual_challenger",
        )
        self.assertIn(
            "figures/ngboost_auto_state_classifier_v1/site_predictions.csv",
            stage.inputs,
        )
        self.assertIn(
            "figures/ngboost_auto_state_residual_v3/site_predictions.csv",
            stage.outputs,
        )
        existing_outputs = {
            path
            for existing in pipeline.STAGES
            if existing.name != stage.name
            for path in existing.outputs
        }
        self.assertTrue(set(stage.outputs).isdisjoint(existing_outputs))

    def test_advisor_package_is_explicit_nonformal_and_last(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-advisor-package"]

        self.assertEqual(names.index(stage.name), len(names) - 1)
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(stage.script, "code/reporting/ootang_advisor_package.py")
        self.assertIn("config/ootang_advisor_package.v1.json", stage.inputs)
        self.assertTrue(
            all(
                path.startswith("figures/advisor_ootang_v1/")
                for path in stage.outputs
            )
        )
        self.assertTrue(
            all(
                "vajont" not in path.lower()
                for path in (*stage.inputs, *stage.outputs)
            )
        )

    def test_skipped_stages_are_removed(self):
        stages = pipeline.select_stages(skipped=["ngboost-shap", "convlstm"])

        self.assertNotIn("ngboost-shap", [stage.name for stage in stages])
        self.assertNotIn("convlstm", [stage.name for stage in stages])
        expected = [
            stage
            for stage in pipeline.STAGES
            if stage.enabled_by_default
            and stage.name not in {"ngboost-shap", "convlstm"}
        ]
        self.assertEqual(stages, expected)

    def test_convlstm_rolling_stage_follows_single_holdout_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        convlstm_stage = pipeline.STAGE_BY_NAME["convlstm"]
        rolling_stage = pipeline.STAGE_BY_NAME["convlstm-rolling"]

        self.assertEqual(names.index("convlstm-rolling"), names.index("convlstm") + 1)
        self.assertIn(
            "figures/convlstm/forecast_run_manifest.json",
            convlstm_stage.outputs,
        )
        self.assertEqual(
            rolling_stage.outputs,
            (
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/rolling_validation_folds.csv",
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/rolling_validation_metrics.csv",
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/rolling_validation_predictions.csv",
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/rolling_seed0/manifest.json",
            ),
        )
        self.assertIn(pipeline.CONVLSTM_PROTOCOL_FILE, rolling_stage.inputs)

    def test_convlstm_seed_stage_follows_rolling_validation(self):
        names = [stage.name for stage in pipeline.STAGES]
        seed_stage = pipeline.STAGE_BY_NAME["convlstm-seeds"]

        self.assertEqual(
            names.index("convlstm-seeds"),
            names.index("convlstm-rolling") + 1,
        )
        self.assertEqual(
            seed_stage.outputs,
            (
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_runs.csv",
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_metrics.csv",
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_summary.csv",
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_training.csv",
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_predictions.csv",
                "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/manifest.json",
            ),
        )
        self.assertIn(pipeline.CONVLSTM_PROTOCOL_FILE, seed_stage.inputs)
        self.assertIn(
            f"{pipeline.CONVLSTM_DIAGNOSTIC_ROOT}/rolling_seed0/manifest.json",
            seed_stage.inputs,
        )

    def test_convlstm_inner_validation_follows_fixed_seed_diagnostic(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["convlstm-inner-validation"]

        self.assertEqual(
            names.index("convlstm-inner-validation"),
            names.index("convlstm-seeds") + 1,
        )
        self.assertIn(
            "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_metrics.csv",
            stage.inputs,
        )
        self.assertEqual(len(stage.outputs), 7)
        self.assertFalse(stage.enabled_by_default)

    def test_convlstm_capacity_stage_follows_inner_validation(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["convlstm-capacity"]

        self.assertEqual(
            names.index("convlstm-capacity"),
            names.index("convlstm-inner-validation") + 1,
        )
        self.assertIn(
            "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/inner_validation_v1/inner_validation_metrics.csv",
            stage.inputs,
        )
        self.assertEqual(len(stage.outputs), 9)
        self.assertFalse(stage.enabled_by_default)

    def test_dry_run_does_not_start_subprocesses(self):
        calls = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest = Path(tmp_dir) / "run.json"
            pipeline.run_pipeline(
                pipeline.select_stages(["features"]),
                dry_run=True,
                runner=lambda *args, **kwargs: calls.append((args, kwargs)),
                manifest_path=manifest,
            )

            self.assertFalse(manifest.exists())

        self.assertEqual(calls, [])

    def test_runner_uses_current_python_and_repository_root(self):
        calls = []

        def record(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        pipeline.run_pipeline(
            pipeline.select_stages(["features"]),
            runner=record,
            verify_contracts=False,
        )

        command, kwargs = calls[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(
            command[1],
            str(ROOT / "code" / "features" / "build_features.py"),
        )
        self.assertEqual(kwargs, {"cwd": ROOT, "check": True})

    def test_failure_stops_later_stages_and_returns_exit_code(self):
        calls = []

        def fail_on_ngboost_shap(command, **kwargs):
            calls.append(Path(command[1]).stem)
            if Path(command[1]).stem == "ngboost_shap":
                raise subprocess.CalledProcessError(7, command)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest = Path(tmp_dir) / "failed.json"
            exit_code = pipeline.main(
                [
                    "--stage",
                    "features",
                    "--stage",
                    "ngboost-shap",
                    "--manifest",
                    str(manifest),
                ],
                runner=fail_on_ngboost_shap,
                verify_contracts=False,
            )
            report = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 7)
        self.assertEqual(calls, ["build_features", "ngboost_shap"])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failed_stage"], "ngboost-shap")
        self.assertEqual(
            [stage["status"] for stage in report["stages"]],
            ["completed", "failed"],
        )

    def test_successful_run_writes_manifest(self):
        def succeed(command, **kwargs):
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest = Path(tmp_dir) / "completed.json"
            pipeline.run_pipeline(
                pipeline.select_stages(["features", "ngboost-shap"]),
                runner=succeed,
                manifest_path=manifest,
                verify_contracts=False,
            )
            report = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["failed_stage"], None)
        self.assertIsInstance(report["git_worktree_dirty"], bool)
        self.assertEqual(len(report["source_sha256"]), 64)
        self.assertEqual(
            [stage["name"] for stage in report["stages"]],
            ["features", "ngboost-shap"],
        )
        self.assertTrue(all(stage["returncode"] == 0 for stage in report["stages"]))

    def test_contract_records_output_fingerprint(self):
        stage = pipeline.Stage(
            "demo",
            "demo.py",
            "demo stage",
            inputs=("input.txt",),
            outputs=("output.txt",),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "input.txt").write_text("input", encoding="utf-8")

            def produce_output(command, **kwargs):
                (root / "output.txt").write_text("result", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)

            report = pipeline.run_pipeline(
                [stage],
                runner=produce_output,
                root=root,
            )

        output = report["stages"][0]["outputs"][0]
        input_artifact = report["stages"][0]["input_artifacts"][0]
        self.assertEqual(report["stages"][0]["contract_status"], "passed")
        self.assertEqual(report["stages"][0]["inputs"], ["input.txt"])
        self.assertEqual(input_artifact["path"], "input.txt")
        self.assertEqual(input_artifact["size_bytes"], 5)
        self.assertEqual(
            input_artifact["sha256"],
            hashlib.sha256(b"input").hexdigest(),
        )
        self.assertEqual(output["path"], "output.txt")
        self.assertEqual(output["size_bytes"], 6)
        self.assertEqual(len(output["sha256"]), 64)

    def test_contract_rejects_missing_input_before_runner(self):
        calls = []
        stage = pipeline.Stage(
            "demo",
            "demo.py",
            "demo stage",
            inputs=("missing.txt",),
            outputs=("output.txt",),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(pipeline.PipelineContractError) as raised:
                pipeline.run_pipeline(
                    [stage],
                    runner=lambda *args, **kwargs: calls.append((args, kwargs)),
                    root=Path(tmp_dir),
                )

        self.assertEqual(raised.exception.kind, "missing_inputs")
        self.assertEqual(calls, [])

    def test_contract_rejects_missing_output_after_successful_process(self):
        stage = pipeline.Stage(
            "demo",
            "demo.py",
            "demo stage",
            outputs=("missing.txt",),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(pipeline.PipelineContractError) as raised:
                pipeline.run_pipeline(
                    [stage],
                    runner=lambda command, **kwargs: subprocess.CompletedProcess(
                        command,
                        0,
                    ),
                    root=Path(tmp_dir),
                )

        self.assertEqual(raised.exception.kind, "missing_outputs")

    def test_contract_rejects_unchanged_stale_output(self):
        stage = pipeline.Stage(
            "demo",
            "demo.py",
            "demo stage",
            outputs=("stale.txt",),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "stale.txt").write_text("old", encoding="utf-8")
            with self.assertRaises(pipeline.PipelineContractError) as raised:
                pipeline.run_pipeline(
                    [stage],
                    runner=lambda command, **kwargs: subprocess.CompletedProcess(
                        command,
                        0,
                    ),
                    root=root,
                )

        self.assertEqual(raised.exception.kind, "unchanged_outputs")

    def test_cli_rejects_unknown_stage(self):
        with self.assertRaises(SystemExit):
            pipeline.main(["--stage", "unknown"])


if __name__ == "__main__":
    unittest.main()
