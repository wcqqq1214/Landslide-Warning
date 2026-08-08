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

        self.assertEqual(
            [stage.name for stage in stages],
            ["features", "convlstm", "ootang-operational-v3"],
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
        stages = pipeline.select_stages(["fusion", "features", "fusion"])

        self.assertEqual([stage.name for stage in stages], ["features", "fusion"])

    def test_skipped_stages_are_removed(self):
        stages = pipeline.select_stages(skipped=["shap", "convlstm"])

        self.assertNotIn("shap", [stage.name for stage in stages])
        self.assertNotIn("convlstm", [stage.name for stage in stages])
        expected = [
            stage
            for stage in pipeline.STAGES
            if stage.enabled_by_default and stage.name not in {"shap", "convlstm"}
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

    def test_shap_stability_stage_follows_base_shap_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["shap-stability"]

        self.assertEqual(names.index("shap-stability"), names.index("shap") + 1)
        self.assertEqual(stage.script, "code/explainability/shap_stability.py")
        self.assertEqual(len(stage.outputs), 12)
        self.assertIn(
            "figures/shap/stability/cross_fold_station_feature_importance.csv",
            stage.outputs,
        )
        self.assertIn(
            "figures/shap/stability/cross_fold_station_feature_stability.csv",
            stage.outputs,
        )

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

    def test_tangent_review_stage_requires_all_station_figures(self):
        stage = pipeline.STAGE_BY_NAME["tangent-review"]

        expected_figures = {
            f"figures/tangent_angle/review/{station}_stage_review.png"
            for station in ("MJ9", "MJ1", "MJ3", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5")
        }

        self.assertTrue(expected_figures.issubset(set(stage.outputs)))
        self.assertIn(
            "figures/tangent_angle/review/candidate_stage_comparison.csv",
            stage.outputs,
        )

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

        def fail_on_onset(command, **kwargs):
            calls.append(Path(command[1]).stem)
            if Path(command[1]).stem == "onset_analysis":
                raise subprocess.CalledProcessError(7, command)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest = Path(tmp_dir) / "failed.json"
            exit_code = pipeline.main(
                [
                    "--stage",
                    "features",
                    "--stage",
                    "onset",
                    "--stage",
                    "shap",
                    "--manifest",
                    str(manifest),
                ],
                runner=fail_on_onset,
                verify_contracts=False,
            )
            report = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 7)
        self.assertEqual(calls, ["build_features", "onset_analysis"])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failed_stage"], "onset")
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
                pipeline.select_stages(["features", "onset"]),
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
            ["features", "onset"],
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
