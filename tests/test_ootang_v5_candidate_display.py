"""Contracts for the display-only Ootang v5 candidate branch."""

from __future__ import annotations

from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
import sys

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import ootang_v5_candidate_display as display  # noqa: E402
from warning import operational_spatial_fusion  # noqa: E402
from warning import operational_v4_fusion  # noqa: E402


class CandidateDisplayProfileTests(unittest.TestCase):
    def test_profile_locks_display_only_scope_and_sources(self):
        profile = display.load_candidate_display_profile()

        self.assertEqual(profile["case"], "ootang")
        self.assertTrue(profile["candidate_display_only"])
        self.assertFalse(profile["v5_fusion_output"])
        self.assertFalse(profile["formal_warning_output"])
        self.assertFalse(profile["vajont_used"])
        contract = profile["source_contract"]
        self.assertTrue(contract["fit_only_v0_selection"])
        self.assertTrue(contract["no_v0_fallback"])
        self.assertTrue(contract["no_manual_override"])
        self.assertTrue(contract["no_v4_fusion_call"])
        self.assertEqual(
            contract["forecast_manifest"],
            "figures/convlstm/forecast_run_manifest.json",
        )
        self.assertEqual(
            contract["v4_manifest"],
            "figures/warning_operational_draft_v4/ootang_operational_run_manifest.json",
        )
        self.assertIn(
            "v5_kinematic_branch_status",
            profile["features"]["availability_fields"],
        )
        self.assertIn(
            "ngboost_inference_or_probability_output", profile["not_claimed"]
        )

    def test_profile_rejects_fallback_fusion_or_ngboost_claim(self):
        profile = display.load_candidate_display_profile()
        changes = (
            (("source_contract", "no_v0_fallback"), False),
            (("v5_fusion_output",), True),
            (("case",), "vajont"),
            (("not_claimed",), ["v5_fused_warning_output"]),
        )
        for path, value in changes:
            changed = json.loads(json.dumps(profile))
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with tempfile.TemporaryDirectory() as directory:
                profile_path = Path(directory) / "profile.json"
                profile_path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(display.CandidateDisplayProfileError):
                    display.load_candidate_display_profile(profile_path)


class CandidateDisplayDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = display.load_candidate_display_profile()
        cls.v0_candidates = pd.read_csv(display.DEFAULT_V0_CANDIDATES_PATH)
        cls.v0_segments = pd.read_csv(display.DEFAULT_V0_SEGMENTS_PATH)
        cls.kinematics = pd.read_csv(
            display.DEFAULT_KINEMATICS_PATH,
            usecols=[
                "case",
                "date",
                "station",
                "velocity",
                "velocity_status",
                "delta_v",
                "delta_v_status",
            ],
        )
        cls.predictions = pd.read_csv(
            display.DEFAULT_PREDICTIONS_PATH,
            usecols=["date", "station", "split", "actual", "p10", "p50", "p90"],
        )
        cls.station = pd.read_csv(
            display.DEFAULT_V4_STATION_PATH,
            usecols=["date", "station", "final_color", "candidate_color"],
        )
        cls.site = pd.read_csv(
            display.DEFAULT_V4_SITE_PATH,
            usecols=[
                "date",
                "site_fusion_status",
                "site_confirmed_color",
                "local_max_candidate_color",
            ],
        )

    def _build(self):
        return display.build_candidate_display_timeline(
            profile=self.profile,
            v0_candidates=self.v0_candidates,
            kinematics=self.kinematics,
            predictions=self.predictions,
            v4_station=self.station,
            v4_site=self.site,
        )

    def test_timeline_keeps_complete_grid_and_gates_v0_fields(self):
        timeline, summary = self._build()

        self.assertEqual(tuple(timeline.columns), display.TIMELINE_COLUMNS)
        self.assertEqual(tuple(summary.columns), display.SUMMARY_COLUMNS)
        self.assertEqual(len(timeline), 4112)
        self.assertEqual(timeline["date"].nunique(), 514)
        self.assertEqual(
            timeline.groupby("station").size().to_dict(),
            {station: 514 for station in display.OOTANG_STATIONS},
        )
        self.assertTrue(
            timeline.groupby("date").size().eq(len(display.OOTANG_STATIONS)).all()
        )
        self.assertEqual(summary["station"].tolist(), list(display.OOTANG_STATIONS))

        available = timeline.loc[timeline["station"].isin(["MJ1", "MJ3"])]
        unavailable = timeline.loc[~timeline["station"].isin(["MJ1", "MJ3"])]
        self.assertTrue(available["v0_candidate_available"].all())
        self.assertTrue(available["candidate_v0_mm_per_day"].gt(0).all())
        self.assertTrue(available["velocity_ratio"].notna().all())
        self.assertTrue(available["tangent_angle_degree"].notna().all())
        self.assertTrue(
            available["v5_kinematic_branch_status"]
            .eq(display.BRANCH_AVAILABLE_STATUS)
            .all()
        )
        self.assertTrue(
            available["velocity_ratio_status"]
            .eq(display.BRANCH_AVAILABLE_STATUS)
            .all()
        )
        self.assertTrue(
            available["tangent_angle_status"]
            .eq(display.BRANCH_AVAILABLE_STATUS)
            .all()
        )

        self.assertTrue((~unavailable["v0_candidate_available"]).all())
        self.assertTrue(unavailable["candidate_v0_mm_per_day"].isna().all())
        self.assertTrue(unavailable["velocity_ratio"].isna().all())
        self.assertTrue(unavailable["tangent_angle_degree"].isna().all())
        for column in (
            "v5_kinematic_branch_status",
            "velocity_ratio_status",
            "tangent_angle_status",
        ):
            self.assertTrue(
                unavailable[column]
                .eq(display.BRANCH_NOT_APPLICABLE_STATUS)
                .all()
            )

        self.assertTrue(timeline["candidate_display_only"].all())
        self.assertFalse(timeline["v5_fusion_output"].any())
        self.assertFalse(timeline["formal_warning_output"].any())
        self.assertFalse(timeline["vajont_used"].any())
        forbidden_outputs = {
            "interval_color",
            "v5_color",
            "v5_level",
            "site_color",
            "site_level",
            "final_color",
            "final_level",
            "candidate_color",
            "candidate_level",
            "ngboost_probability",
        }
        self.assertTrue(forbidden_outputs.isdisjoint(timeline.columns))
        self.assertEqual(
            [
                column
                for column in timeline.columns
                if column.endswith("_color")
                and not column.startswith("v4_reference_")
            ],
            [],
        )

    def test_timeline_uses_result_splits_and_fit_only_tolerance(self):
        timeline, _ = self._build()

        self.assertEqual(set(timeline["split"]), {"calibration", "test"})
        result_start = pd.to_datetime(timeline["date"]).min()
        self.assertTrue(
            (pd.to_datetime(self.v0_candidates["fit_end_date"]) < result_start).all()
        )
        forbidden_selection_fields = {
            "split",
            "calibration_start_date",
            "calibration_end_date",
            "test_start_date",
            "test_end_date",
            "manual_stage_start_date",
            "manual_stage_end_date",
        }
        self.assertTrue(
            forbidden_selection_fields.isdisjoint(self.v0_candidates.columns)
        )
        self.assertTrue(forbidden_selection_fields.isdisjoint(self.v0_segments.columns))

        source = self.kinematics.copy()
        source["date"] = pd.to_datetime(source["date"])
        for station in display.OOTANG_STATIONS:
            candidate = self.v0_candidates.loc[
                self.v0_candidates["station"].eq(station)
            ].iloc[0]
            fit = source.loc[
                source["station"].eq(station)
                & source["date"].le(pd.Timestamp(candidate["fit_end_date"]))
                & source["delta_v_status"].eq("valid")
            ]
            values = pd.to_numeric(fit["delta_v"], errors="coerce").dropna()
            median = values.median()
            expected = 1.4826 * (values - median).abs().median()
            observed = timeline.loc[
                timeline["station"].eq(station),
                "delta_v_near_zero_tau_mm_per_day",
            ]
            np.testing.assert_allclose(observed.to_numpy(), expected)

    def test_unavailable_v0_imputation_is_rejected(self):
        changed = self.v0_candidates.copy()
        changed.loc[changed["station"].eq("ATU1"), "candidate_v0_mm_per_day"] = 1.0

        with self.assertRaises(display.CandidateDisplayInputError):
            display.build_candidate_display_timeline(
                profile=self.profile,
                v0_candidates=changed,
                kinematics=self.kinematics,
                predictions=self.predictions,
                v4_station=self.station,
                v4_site=self.site,
            )

    def test_v4_fusion_functions_are_not_called(self):
        with (
            patch.object(
                operational_v4_fusion,
                "fuse_station_evidence_families_v4",
                side_effect=AssertionError("v4 station fusion called"),
            ) as station_fusion,
            patch.object(
                operational_spatial_fusion,
                "fuse_site_spatial_blocks",
                side_effect=AssertionError("v4 site fusion called"),
            ) as site_fusion,
        ):
            self._build()

        station_fusion.assert_not_called()
        site_fusion.assert_not_called()

    def test_candidate_inputs_exclude_acceleration_environment_and_other_cases(self):
        timeline, _ = self._build()
        forbidden_columns = {
            "acceleration",
            "acceleration_status",
            "rainfall",
            "reservoir_water_level",
            "groundwater_level",
            "temperature",
            "coordinates",
            "elevation",
        }
        self.assertTrue(forbidden_columns.isdisjoint(timeline.columns))
        paths = [
            value
            for value in self.profile["source_contract"].values()
            if isinstance(value, str)
        ]
        self.assertTrue(all("vajont" not in path.lower() for path in paths))


class CandidateDisplayMaterializedTests(unittest.TestCase):
    def test_materialized_bundle_is_isolated_and_auditable(self):
        output_dir = display.DEFAULT_OUTPUT_DIR
        manifest = json.loads((output_dir / "manifest.json").read_text())

        self.assertEqual(manifest["case"], "ootang")
        self.assertTrue(manifest["candidate_display_only"])
        self.assertFalse(manifest["candidate_warning_color_output"])
        self.assertFalse(manifest["ngboost_inference_output"])
        self.assertFalse(manifest["v5_fusion_output"])
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertEqual(manifest["timeline_rows"], 4112)
        self.assertEqual(manifest["timeline_dates"], 514)
        self.assertEqual(manifest["summary_rows"], 8)
        self.assertEqual(manifest["v0_available_rows"], 1028)
        self.assertEqual(manifest["v0_not_applicable_rows"], 3084)
        self.assertEqual(manifest["v0_available_stations"], ["MJ1", "MJ3"])
        self.assertEqual(len(manifest["v0_unavailable_stations"]), 6)
        self.assertEqual(
            manifest["branch_status_counts"],
            {
                display.BRANCH_AVAILABLE_STATUS: 1028,
                display.BRANCH_NOT_APPLICABLE_STATUS: 3084,
            },
        )
        self.assertTrue(
            manifest["source_inputs"]["v0_manifest"][
                "fit_only_contract_verified"
            ]
        )
        self.assertIn(
            "reference_outputs", manifest["source_inputs"]["v4_manifest"]
        )
        self.assertIn(
            "ngboost_inference_or_probability_output", manifest["not_claimed"]
        )
        self.assertIn("git_commit", manifest)
        self.assertIsInstance(manifest["git_worktree_dirty"], (bool, type(None)))
        runner_record = manifest["implementation_sources"]["runner"]
        runner_path = Path(runner_record["path"])
        if not runner_path.is_absolute():
            runner_path = ROOT / runner_path
        self.assertEqual(
            hashlib.sha256(runner_path.read_bytes()).hexdigest(),
            runner_record["sha256"],
        )

        timeline = pd.read_csv(output_dir / "candidate_timeline.csv")
        summary = pd.read_csv(output_dir / "candidate_summary.csv")
        self.assertEqual(len(timeline), 4112)
        self.assertEqual(len(summary), 8)
        self.assertEqual(tuple(timeline.columns), display.TIMELINE_COLUMNS)
        self.assertEqual(tuple(summary.columns), display.SUMMARY_COLUMNS)
        for record in manifest["outputs"].values():
            path = Path(record["path"])
            if not path.is_absolute():
                path = ROOT / path
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"]
            )
        svg_lines = (output_dir / "candidate_display.svg").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertFalse(any(line.rstrip() != line for line in svg_lines))

    def test_bundle_is_deterministic(self):
        names = [
            "candidate_timeline.csv",
            "candidate_summary.csv",
            "candidate_display.png",
            "candidate_display.svg",
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            display.write_candidate_display(output_dir=output)
            for name in names:
                self.assertEqual(
                    hashlib.sha256(
                        (display.DEFAULT_OUTPUT_DIR / name).read_bytes()
                    ).hexdigest(),
                    hashlib.sha256((output / name).read_bytes()).hexdigest(),
                    name,
                )

    def test_writing_bundle_does_not_modify_v4_outputs(self):
        protected_paths = (
            display.DEFAULT_V4_STATION_PATH,
            display.DEFAULT_V4_SITE_PATH,
            display.DEFAULT_V4_MANIFEST_PATH,
            display.DEFAULT_THRESHOLDS_PATH,
        )
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in protected_paths
        }

        with tempfile.TemporaryDirectory() as directory:
            display.write_candidate_display(output_dir=Path(directory) / "bundle")

        after = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in protected_paths
        }
        self.assertEqual(after, before)

    def test_output_override_and_cli_override_are_rejected(self):
        with self.assertRaises(display.CandidateDisplayOutputError):
            display.write_candidate_display(output_dir=ROOT / "figures" / "convlstm")
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                display._parse_args(["--output-dir", "figures/convlstm"])


class CandidateDisplayReportTests(unittest.TestCase):
    def test_report_uses_candidate_artifacts_and_preserves_nonclaims(self):
        report = (ROOT / "paper" / "process_report.tex").read_text(encoding="utf-8")

        self.assertIn(
            "../figures/v5_candidate_display_ootang_v1/candidate_display.png",
            report,
        )
        self.assertIn("\\label{tab:v5-candidate-display}", report)
        self.assertIn("不运行新的 NGBoost 推断", report)
        self.assertIn("不输出 v5 融合或正式预警", report)
        for unpublished_name in (
            "advisor_review_action_plan",
            "review.md",
            "ootang_stable_segment_expert_review",
            "ootang_interval_calibration_expert_review",
        ):
            self.assertNotIn(unpublished_name, report)

    def test_report_builds_with_candidate_figure(self):
        latexmk = shutil.which("latexmk")
        xelatex = shutil.which("xelatex")
        if latexmk is None or xelatex is None:
            self.skipTest("latexmk and xelatex are required for report build coverage")

        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    latexmk,
                    "-xelatex",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    f"-outdir={directory}",
                    "process_report.tex",
                ],
                cwd=ROOT / "paper",
                capture_output=True,
                text=True,
                check=False,
            )
            output = f"{result.stdout}\n{result.stderr}"
            self.assertEqual(result.returncode, 0, output[-8000:])
            self.assertTrue((Path(directory) / "process_report.pdf").is_file())
            log_path = Path(directory) / "process_report.log"
            self.assertTrue(log_path.is_file())
            log = log_path.read_text(encoding="utf-8", errors="replace")
            for failure_marker in (
                "LaTeX Error",
                "Float too large",
                "There were undefined references",
            ):
                self.assertNotIn(failure_marker, log)


if __name__ == "__main__":
    unittest.main()
