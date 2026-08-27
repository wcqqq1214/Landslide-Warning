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

        self.assertEqual(len(pipeline.STAGES), 35)
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
        self.assertFalse(any(path.startswith("models/") for path in stage.outputs))

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

    def test_prequential_monitor_is_explicit_and_uses_only_oof_bundle(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-prequential-monitor"]

        self.assertEqual(
            names.index(stage.name),
            names.index("convlstm-capacity") + 1,
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "retrospective_prequential_monitoring_research",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_prequential_monitor.py",
        )
        self.assertIn(
            "figures/convlstm/runs/displacement_elevation_exog_v1/"
            "fixed120_v1/seed_stability_0_4/seed_stability_predictions.csv",
            stage.inputs,
        )
        self.assertNotIn("figures/convlstm/forecast_predictions.csv", stage.inputs)
        self.assertTrue(
            all(
                path.startswith("figures/prequential_anomaly_ootang_v1/")
                for path in stage.outputs
            )
        )
        forbidden = ("warning_color", "event_recall", "far", "vajont")
        self.assertFalse(
            any(
                token in path.lower()
                for token in forbidden
                for path in (*stage.inputs, *stage.outputs)
            )
        )

    def test_prequential_calibration_bakeoff_is_explicit_and_uses_e1_bundle(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-prequential-calibration-bakeoff"]

        self.assertEqual(
            names.index(stage.name),
            names.index("ootang-prequential-monitor") + 1,
        )
        self.assertEqual(
            names.index("ootang-live-source"),
            names.index(stage.name) + 1,
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "retrospective_prequential_calibration_bakeoff_research",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_prequential_calibration_bakeoff.py",
        )
        self.assertEqual(
            stage.inputs,
            (
                "config/ootang_prequential_calibration_bakeoff.v1.json",
                "config/ootang_prequential_monitor.v1.json",
                "figures/prequential_anomaly_ootang_v1/station_timeline.csv",
                "figures/prequential_anomaly_ootang_v1/site_timeline.csv",
                "figures/prequential_anomaly_ootang_v1/prequential_metrics.csv",
                "figures/prequential_anomaly_ootang_v1/manifest.json",
            ),
        )
        self.assertNotIn("figures/convlstm/forecast_predictions.csv", stage.inputs)
        self.assertEqual(
            stage.outputs,
            (
                "figures/prequential_calibration_bakeoff_ootang_v1/"
                "candidate_timeline.csv",
                "figures/prequential_calibration_bakeoff_ootang_v1/"
                "candidate_metrics.csv",
                "figures/prequential_calibration_bakeoff_ootang_v1/"
                "pairwise_comparison.csv",
                "figures/prequential_calibration_bakeoff_ootang_v1/manifest.json",
            ),
        )
        self.assertEqual(
            stage.arguments,
            (
                "--config",
                "config/ootang_prequential_calibration_bakeoff.v1.json",
            ),
        )

    def test_e2b_engineering_stages_are_explicit_and_ordered_before_live(self):
        names = [stage.name for stage in pipeline.STAGES]
        expected = (
            (
                "ootang-live-source",
                "code/monitoring/ootang_live_source.py",
                "runtime/ootang_prequential_live_v1/source_ingest_status.json",
                ("data/monitoring_data.csv",),
            ),
            (
                "ootang-production-bundle",
                "code/convlstm/ootang_production_bundle.py",
                "runtime/ootang_prequential_live_v1/model_bundle_status.json",
                (
                    "data/monitoring_data.csv",
                    "data/station_coords.csv",
                    "pyproject.toml",
                    "uv.lock",
                ),
            ),
            (
                "ootang-issue-producer",
                "code/monitoring/ootang_issue_producer.py",
                "runtime/ootang_prequential_live_v1/issue_producer_status.json",
                (
                    "data/monitoring_data.csv",
                    "data/station_coords.csv",
                    "pyproject.toml",
                    "uv.lock",
                ),
            ),
        )

        self.assertEqual(
            names.index("ootang-live-source"),
            names.index("ootang-prequential-calibration-bakeoff") + 1,
        )
        for (name, script, output, static_inputs), next_name in zip(
            expected,
            (
                "ootang-production-bundle",
                "ootang-issue-producer",
                "ootang-outcome-materializer",
            ),
        ):
            stage = pipeline.STAGE_BY_NAME[name]
            self.assertEqual(names.index(next_name), names.index(name) + 1)
            self.assertFalse(stage.enabled_by_default)
            self.assertFalse(stage.formal_warning_output)
            self.assertEqual(
                stage.warning_artifact_scope,
                "live_prequential_deployment_engineering",
            )
            self.assertEqual(stage.script, script)
            self.assertEqual(stage.outputs, (output,))
            self.assertEqual(
                stage.arguments,
                ("--config", "config/ootang_prequential_deploy.v1.json"),
            )
            self.assertIn("config/ootang_prequential_deploy.v1.json", stage.inputs)
            for static_input in static_inputs:
                self.assertIn(static_input, stage.inputs)

    def test_e2b2_outcome_and_cycle_are_explicit_machine_only_stages(self):
        names = [stage.name for stage in pipeline.STAGES]
        outcome = pipeline.STAGE_BY_NAME["ootang-outcome-materializer"]
        cycle = pipeline.STAGE_BY_NAME["ootang-prequential-cycle"]

        self.assertEqual(
            names.index("ootang-outcome-materializer"),
            names.index("ootang-issue-producer") + 1,
        )
        self.assertEqual(
            names.index("ootang-prequential-cycle"),
            names.index("ootang-prequential-live") + 1,
        )
        for stage, script, output in (
            (
                outcome,
                "code/monitoring/ootang_outcome_materializer.py",
                "runtime/ootang_prequential_live_v1/outcome_materializer_status.json",
            ),
            (
                cycle,
                "code/monitoring/ootang_prequential_cycle.py",
                "runtime/ootang_prequential_live_v1/cycle_status.json",
            ),
        ):
            self.assertFalse(stage.enabled_by_default)
            self.assertFalse(stage.formal_warning_output)
            self.assertEqual(
                stage.warning_artifact_scope,
                "live_prequential_cycle_engineering",
            )
            self.assertEqual(stage.script, script)
            self.assertEqual(stage.outputs, (output,))
            self.assertEqual(
                stage.arguments,
                ("--config", "config/ootang_prequential_cycle.v1.json"),
            )
            self.assertIn("config/ootang_prequential_cycle.v1.json", stage.inputs)
            self.assertIn("config/ootang_prequential_deploy.v1.json", stage.inputs)
            self.assertIn("config/ootang_prequential_live.v1.json", stage.inputs)

    def test_calibration_shadow_and_cycle_v2_are_explicit_machine_only_stages(self):
        names = [stage.name for stage in pipeline.STAGES]
        shadow = pipeline.STAGE_BY_NAME["ootang-prequential-calibration-shadow"]
        cycle_v2 = pipeline.STAGE_BY_NAME["ootang-prequential-cycle-v2"]

        self.assertEqual(
            names.index(shadow.name),
            names.index("ootang-prequential-cycle") + 1,
        )
        self.assertEqual(names.index(cycle_v2.name), names.index(shadow.name) + 1)
        self.assertEqual(
            shadow.script,
            "code/monitoring/ootang_prequential_calibration_shadow.py",
        )
        self.assertEqual(
            shadow.outputs,
            ("runtime/ootang_prequential_calibration_shadow_v1/status.json",),
        )
        self.assertEqual(
            shadow.arguments,
            (
                "--config",
                "config/ootang_prequential_calibration_shadow.v1.json",
            ),
        )
        self.assertEqual(
            cycle_v2.script,
            "code/monitoring/ootang_prequential_cycle_v2.py",
        )
        self.assertEqual(
            cycle_v2.outputs,
            ("runtime/ootang_prequential_live_v1/cycle_v2_status.json",),
        )
        self.assertEqual(
            cycle_v2.arguments,
            ("--config", "config/ootang_prequential_cycle.v2.json"),
        )
        for stage in (shadow, cycle_v2):
            self.assertFalse(stage.enabled_by_default)
            self.assertFalse(stage.formal_warning_output)
        self.assertIn(
            "config/ootang_prequential_calibration_shadow.v1.json",
            cycle_v2.inputs,
        )
        self.assertIn("config/ootang_prequential_cycle.v1.json", cycle_v2.inputs)

    def test_replay_guard_and_cycle_v3_are_explicit_machine_only_stages(self):
        names = [stage.name for stage in pipeline.STAGES]
        replay = pipeline.STAGE_BY_NAME["ootang-issue-replay"]
        verified = pipeline.STAGE_BY_NAME["ootang-verified-live"]
        cycle_v3 = pipeline.STAGE_BY_NAME["ootang-prequential-cycle-v3"]

        self.assertEqual(
            names.index(replay.name),
            names.index("ootang-prequential-cycle-v2") + 1,
        )
        self.assertEqual(names.index(verified.name), names.index(replay.name) + 1)
        self.assertEqual(names.index(cycle_v3.name), names.index(verified.name) + 1)
        expected = (
            (
                replay,
                "code/monitoring/ootang_issue_replay.py",
                "config/ootang_issue_replay.v1.json",
                "runtime/ootang_prequential_live_v1/issue_replay_status.json",
            ),
            (
                verified,
                "code/monitoring/ootang_verified_live.py",
                "config/ootang_verified_live.v1.json",
                "runtime/ootang_prequential_live_v1/verified_live_status.json",
            ),
            (
                cycle_v3,
                "code/monitoring/ootang_prequential_cycle_v3.py",
                "config/ootang_prequential_cycle.v3.json",
                "runtime/ootang_prequential_live_v1/cycle_v3_status.json",
            ),
        )
        for stage, script, config, output in expected:
            self.assertFalse(stage.enabled_by_default)
            self.assertFalse(stage.formal_warning_output)
            self.assertEqual(stage.script, script)
            self.assertEqual(stage.outputs, (output,))
            self.assertEqual(stage.arguments, ("--config", config))
            self.assertIn(config, stage.inputs)
        self.assertIn("config/ootang_prequential_cycle.v2.json", cycle_v3.inputs)
        self.assertIn("config/ootang_issue_replay.v1.json", cycle_v3.inputs)
        self.assertIn("config/ootang_verified_live.v1.json", cycle_v3.inputs)
        self.assertIn(
            "config/ootang_prequential_calibration_shadow.v1.json",
            cycle_v3.inputs,
        )

    def test_trusted_time_shadow_is_explicit_machine_only_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-trusted-time-shadow"]

        self.assertEqual(
            names.index(stage.name),
            names.index("ootang-prequential-cycle-v3") + 1,
        )
        self.assertEqual(
            names.index("ootang-epoch-registry"), names.index(stage.name) + 1
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "live_prequential_trusted_time_shadow_engineering",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_trusted_time_shadow.py",
        )
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_trusted_time_shadow.v1.json"),
        )
        self.assertEqual(
            stage.outputs,
            ("runtime/ootang_prequential_live_v1/trusted_time_shadow_status.json",),
        )
        for required in (
            "config/ootang_trusted_time_shadow.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/trust/sigstore_tsa_2025_manifest.v1.json",
            "config/trust/sigstore_tsa_2025_leaf.pem",
            "config/trust/sigstore_tsa_2025_root.pem",
            "code/monitoring/ootang_trusted_time_shadow_core.py",
            "tools/ootang_trusted_time_runtime/pyproject.toml",
            "tools/ootang_trusted_time_runtime/uv.lock",
        ):
            self.assertIn(required, stage.inputs)

        ordered = pipeline.select_stages(
            ["ootang-trusted-time-shadow", "ootang-prequential-cycle-v3"]
        )
        self.assertEqual(
            [selected.name for selected in ordered],
            ["ootang-prequential-cycle-v3", "ootang-trusted-time-shadow"],
        )

    def test_epoch_registry_is_explicit_r1_prebuild_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-epoch-registry"]

        self.assertEqual(
            names.index(stage.name),
            names.index("ootang-trusted-time-shadow") + 1,
        )
        self.assertEqual(
            names.index("ootang-epoch-preparation"), names.index(stage.name) + 1
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "epoch_candidate_registry_r1_engineering",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_epoch_registry.py",
        )
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_epoch_registry.v1.json"),
        )
        self.assertEqual(
            stage.outputs,
            ("runtime/ootang_epoch_registry_v1/registry_status.json",),
        )
        for required in (
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "config/trust/sigstore_tsa_2025_manifest.v1.json",
            "config/trust/sigstore_tsa_2025_leaf.pem",
            "config/trust/sigstore_tsa_2025_root.pem",
            "pyproject.toml",
            "uv.lock",
            "tools/ootang_trusted_time_runtime/pyproject.toml",
            "tools/ootang_trusted_time_runtime/uv.lock",
        ):
            self.assertIn(required, stage.inputs)

        ordered = pipeline.select_stages(
            ["ootang-epoch-registry", "ootang-trusted-time-shadow"]
        )
        self.assertEqual(
            [selected.name for selected in ordered],
            ["ootang-trusted-time-shadow", "ootang-epoch-registry"],
        )

    def test_epoch_preparation_is_explicit_same_origin_r2a_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-epoch-preparation"]

        self.assertEqual(
            names.index(stage.name), names.index("ootang-epoch-registry") + 1
        )
        self.assertEqual(names.index("ootang-epoch-drain"), names.index(stage.name) + 1)
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "epoch_candidate_same_origin_preflight_r2a_engineering",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_epoch_preparation.py",
        )
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_epoch_preparation.v1.json"),
        )
        self.assertEqual(
            stage.outputs,
            ("runtime/ootang_epoch_registry_v1/preparation_status.json",),
        )
        for required in (
            "config/ootang_epoch_preparation.v1.json",
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "code/convlstm/__init__.py",
            "code/monitoring/__init__.py",
            "pyproject.toml",
            "uv.lock",
            "tools/ootang_trusted_time_runtime/pyproject.toml",
            "tools/ootang_trusted_time_runtime/uv.lock",
        ):
            self.assertIn(required, stage.inputs)

        ordered = pipeline.select_stages(
            ["ootang-epoch-preparation", "ootang-epoch-registry"]
        )
        self.assertEqual(
            [selected.name for selected in ordered],
            ["ootang-epoch-registry", "ootang-epoch-preparation"],
        )

    def test_epoch_drain_is_explicit_machine_r2b_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-epoch-drain"]

        self.assertEqual(
            names.index(stage.name), names.index("ootang-epoch-preparation") + 1
        )
        self.assertEqual(
            names.index("ootang-epoch-drain-eligibility"),
            names.index(stage.name) + 1,
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "epoch_drain_barrier_r2b_engineering",
        )
        self.assertEqual(stage.script, "code/monitoring/ootang_epoch_drain.py")
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_epoch_drain.v1.json"),
        )
        self.assertEqual(
            stage.outputs,
            ("runtime/ootang_epoch_registry_v1/drain_status.json",),
        )
        for required in (
            "config/ootang_epoch_drain.v1.json",
            "config/ootang_epoch_preparation.v1.json",
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
        ):
            self.assertIn(required, stage.inputs)

        ordered = pipeline.select_stages(
            ["ootang-epoch-drain", "ootang-epoch-preparation"]
        )
        self.assertEqual(
            [selected.name for selected in ordered],
            ["ootang-epoch-preparation", "ootang-epoch-drain"],
        )

    def test_epoch_drain_eligibility_is_explicit_machine_r2b_2a_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-epoch-drain-eligibility"]

        self.assertEqual(names.index(stage.name), names.index("ootang-epoch-drain") + 1)
        self.assertEqual(
            names.index("ootang-epoch-drain-v2-workset"),
            names.index(stage.name) + 1,
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "epoch_drain_eligibility_observation_r2b_2a_engineering",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_epoch_drain_eligibility.py",
        )
        self.assertEqual(
            stage.arguments,
            (
                "--config",
                "config/ootang_epoch_drain_eligibility.v1.json",
            ),
        )
        self.assertEqual(
            stage.outputs,
            ("runtime/ootang_epoch_registry_v1/drain_eligibility_status.json",),
        )
        for required in (
            "config/ootang_epoch_drain_eligibility.v1.json",
            "config/ootang_epoch_drain.v1.json",
            "config/ootang_epoch_preparation.v1.json",
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "code/monitoring/ootang_epoch_drain.py",
        ):
            self.assertIn(required, stage.inputs)

        ordered = pipeline.select_stages(
            ["ootang-epoch-drain-eligibility", "ootang-epoch-drain"]
        )
        self.assertEqual(
            [selected.name for selected in ordered],
            ["ootang-epoch-drain", "ootang-epoch-drain-eligibility"],
        )

    def test_epoch_drain_v2_workset_is_explicit_machine_r2b_2b_1_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-epoch-drain-v2-workset"]

        self.assertEqual(
            names.index(stage.name),
            names.index("ootang-epoch-drain-eligibility") + 1,
        )
        self.assertEqual(
            names.index("ootang-epoch-admission-cut"), names.index(stage.name) + 1
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.description,
            "观察机器当前旧 epoch 首个排空阻塞项（R2b-2b-1，非 DRAINING authority/非切换/非正式）",
        )
        self.assertEqual(
            stage.warning_artifact_scope,
            "epoch_drain_first_blocker_observation_r2b_2b_1_v2_engineering",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_epoch_drain_v2.py",
        )
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_epoch_drain.v2.json"),
        )
        self.assertEqual(
            stage.outputs,
            ("runtime/ootang_epoch_registry_v1/drain_v2/status.json",),
        )
        for required in (
            "config/ootang_epoch_drain.v2.json",
            "config/ootang_epoch_drain.v1.json",
            "config/ootang_epoch_drain_eligibility.v1.json",
            "config/ootang_epoch_preparation.v1.json",
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "code/monitoring/ootang_epoch_drain.py",
            "code/monitoring/ootang_epoch_drain_eligibility.py",
            "code/monitoring/ootang_issue_replay.py",
            "code/monitoring/ootang_verified_live.py",
            "code/monitoring/ootang_prequential_calibration_shadow.py",
            "code/monitoring/ootang_trusted_time_shadow_core.py",
        ):
            self.assertIn(required, stage.inputs)

        ordered = pipeline.select_stages(
            [
                "ootang-operational-v4",
                "ootang-epoch-admission-cut",
                "ootang-epoch-drain-v2-workset",
                "ootang-epoch-drain-eligibility",
            ]
        )
        self.assertEqual(
            [selected.name for selected in ordered],
            [
                "ootang-epoch-drain-eligibility",
                "ootang-epoch-drain-v2-workset",
                "ootang-epoch-admission-cut",
                "ootang-operational-v4",
            ],
        )

    def test_epoch_admission_cut_is_explicit_machine_r2b_2b_2a_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-epoch-admission-cut"]

        self.assertEqual(
            names.index(stage.name),
            names.index("ootang-epoch-drain-v2-workset") + 1,
        )
        self.assertEqual(
            names.index("ootang-epoch-workset-manifest"), names.index(stage.name) + 1
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "epoch_admission_writer_lock_cut_r2b_2b_2a_engineering",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_epoch_admission_cut.py",
        )
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_epoch_admission_cut.v1.json"),
        )
        self.assertEqual(
            stage.outputs,
            ("runtime/ootang_epoch_registry_v1/admission_cut_v1/status.json",),
        )
        for required in (
            "config/ootang_epoch_admission_cut.v1.json",
            "config/ootang_epoch_drain.v2.json",
            "config/ootang_epoch_drain.v1.json",
            "code/monitoring/ootang_epoch_drain_v2.py",
            "code/monitoring/ootang_epoch_drain.py",
            "code/monitoring/ootang_live_source.py",
            "code/monitoring/ootang_issue_producer.py",
            "code/monitoring/ootang_outcome_materializer.py",
            "code/monitoring/ootang_prequential_live.py",
            "code/monitoring/ootang_verified_live.py",
            "code/monitoring/ootang_issue_replay.py",
            "code/monitoring/ootang_trusted_time_shadow_core.py",
            "code/monitoring/ootang_prequential_calibration_shadow.py",
            "code/monitoring/ootang_prequential_cycle.py",
            "code/monitoring/ootang_prequential_cycle_v2.py",
            "code/monitoring/ootang_prequential_cycle_v3.py",
        ):
            self.assertIn(required, stage.inputs)

        ordered = pipeline.select_stages(
            [
                "ootang-operational-v4",
                "ootang-epoch-workset-manifest",
                "ootang-epoch-admission-cut",
                "ootang-epoch-drain-v2-workset",
            ]
        )
        self.assertEqual(
            [selected.name for selected in ordered],
            [
                "ootang-epoch-drain-v2-workset",
                "ootang-epoch-admission-cut",
                "ootang-epoch-workset-manifest",
                "ootang-operational-v4",
            ],
        )

    def test_epoch_workset_manifest_is_explicit_machine_r2b_2b_2b_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-epoch-workset-manifest"]

        self.assertEqual(
            names.index(stage.name), names.index("ootang-epoch-admission-cut") + 1
        )
        self.assertEqual(
            names.index("ootang-epoch-workset-recovery"), names.index(stage.name) + 1
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "epoch_closed_workset_manifest_reservation_r2b_2b_2b_engineering",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_epoch_workset_manifest.py",
        )
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_epoch_workset_manifest.v1.json"),
        )
        self.assertEqual(
            stage.outputs,
            ("runtime/ootang_epoch_registry_v1/workset_manifest_v1/status.json",),
        )
        for required in (
            "config/ootang_epoch_workset_manifest.v1.json",
            "config/ootang_epoch_admission_cut.v1.json",
            "config/ootang_epoch_drain.v2.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "code/monitoring/ootang_epoch_admission_cut.py",
            "code/monitoring/ootang_epoch_drain_v2.py",
            "code/monitoring/ootang_epoch_workset_inventory.py",
            "code/monitoring/ootang_live_source.py",
            "code/monitoring/ootang_issue_producer.py",
            "code/monitoring/ootang_outcome_materializer.py",
            "code/monitoring/ootang_prequential_live.py",
            "code/monitoring/ootang_verified_live.py",
            "code/monitoring/ootang_issue_replay.py",
            "code/monitoring/ootang_trusted_time_shadow_core.py",
            "code/monitoring/ootang_prequential_calibration_shadow.py",
        ):
            self.assertIn(required, stage.inputs)

        ordered = pipeline.select_stages(
            [
                "ootang-operational-v4",
                "ootang-epoch-workset-manifest",
                "ootang-epoch-admission-cut",
            ]
        )
        self.assertEqual(
            [selected.name for selected in ordered],
            [
                "ootang-epoch-admission-cut",
                "ootang-epoch-workset-manifest",
                "ootang-operational-v4",
            ],
        )

    def test_epoch_workset_recovery_is_explicit_machine_r2b_2b_2c_stage(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-epoch-workset-recovery"]

        self.assertEqual(
            names.index(stage.name), names.index("ootang-epoch-workset-manifest") + 1
        )
        self.assertEqual(
            names.index("ootang-operational-v4"), names.index(stage.name) + 1
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "epoch_manifest_keyed_deterministic_local_recovery_r2b_2b_2c_engineering",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_epoch_workset_recovery.py",
        )
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_epoch_workset_recovery.v1.json"),
        )
        self.assertEqual(
            stage.outputs,
            ("runtime/ootang_epoch_registry_v1/workset_recovery_v1/status.json",),
        )
        for required in (
            "config/ootang_epoch_workset_recovery.v1.json",
            "config/ootang_epoch_workset_manifest.v1.json",
            "config/ootang_epoch_admission_cut.v1.json",
            "config/ootang_epoch_drain.v2.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "code/monitoring/ootang_epoch_workset_recovery.py",
            "code/monitoring/ootang_epoch_workset_manifest.py",
            "code/monitoring/ootang_epoch_admission_cut.py",
            "code/monitoring/ootang_epoch_drain.py",
            "code/monitoring/ootang_epoch_drain_v2.py",
            "code/monitoring/ootang_epoch_registry.py",
            "code/monitoring/ootang_prequential_live.py",
            "code/monitoring/ootang_verified_live.py",
            "code/monitoring/ootang_trusted_time_shadow_core.py",
        ):
            self.assertIn(required, stage.inputs)

        ordered = pipeline.select_stages(
            [
                "ootang-operational-v4",
                "ootang-epoch-workset-recovery",
                "ootang-epoch-workset-manifest",
                "ootang-epoch-admission-cut",
            ]
        )
        self.assertEqual(
            [selected.name for selected in ordered],
            [
                "ootang-epoch-admission-cut",
                "ootang-epoch-workset-manifest",
                "ootang-epoch-workset-recovery",
                "ootang-operational-v4",
            ],
        )

    def test_prequential_live_is_explicit_engineering_after_outcome_materializer(self):
        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-prequential-live"]

        self.assertEqual(
            names.index(stage.name),
            names.index("ootang-outcome-materializer") + 1,
        )
        self.assertFalse(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(
            stage.warning_artifact_scope,
            "live_prequential_monitoring_engineering",
        )
        self.assertEqual(
            stage.script,
            "code/monitoring/ootang_prequential_live.py",
        )
        self.assertEqual(
            stage.inputs,
            (
                "config/ootang_prequential_live.v1.json",
                "config/ootang_prequential_monitor.v1.json",
                "figures/prequential_anomaly_ootang_v1/manifest.json",
            ),
        )
        self.assertEqual(
            stage.arguments,
            ("--config", "config/ootang_prequential_live.v1.json"),
        )
        self.assertEqual(
            stage.outputs,
            ("runtime/ootang_prequential_live_v1/status.json",),
        )
        self.assertNotIn(
            "runtime/ootang_prequential_live_v1/ledger.sqlite3",
            stage.outputs,
        )

    def test_prequential_live_runner_receives_explicit_config_argument(self):
        calls = []

        def record(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        pipeline.run_pipeline(
            pipeline.select_stages(["ootang-prequential-live"]),
            runner=record,
            verify_contracts=False,
        )

        command, kwargs = calls[0]
        self.assertEqual(
            command,
            [
                sys.executable,
                str(ROOT / "code" / "monitoring" / "ootang_prequential_live.py"),
                "--config",
                "config/ootang_prequential_live.v1.json",
            ],
        )
        self.assertEqual(kwargs, {"cwd": ROOT, "check": True})

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
