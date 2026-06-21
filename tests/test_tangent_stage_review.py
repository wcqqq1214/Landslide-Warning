import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import tangent_angle  # noqa: E402
import features  # noqa: E402


class ReferenceStageLoadingTests(unittest.TestCase):
    def test_load_reference_stages_accepts_valid_csv(self):
        csv_path = ROOT / "config" / "tangent_reference_stages.csv"
        stages = tangent_angle.load_reference_stages(csv_path)

        self.assertIn("station", stages.columns)
        self.assertIn("status", stages.columns)
        self.assertIn("source", stages.columns)
        self.assertTrue(len(stages) > 0)
        self.assertTrue(
            stages["status"].isin(["candidate", "approved", "rejected"]).all()
        )

    def test_load_reference_stages_rejects_missing_required_columns(self):
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as tmp:
            tmp.write("station,start_date\nMJ9,2020-01-01\n")
            tmp_path = tmp.name

        try:
            with self.assertRaisesRegex(ValueError, "缺少必需列"):
                tangent_angle.load_reference_stages(tmp_path)
        finally:
            Path(tmp_path).unlink()

    def test_load_reference_stages_rejects_invalid_status(self):
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as tmp:
            tmp.write(
                "station,start_date,end_date,status,source,review_note\n"
                "MJ9,2020-01-01,2020-01-10,invalid,automatic_15d,\n"
            )
            tmp_path = tmp.name

        try:
            with self.assertRaisesRegex(ValueError, "无效的阶段状态"):
                tangent_angle.load_reference_stages(tmp_path)
        finally:
            Path(tmp_path).unlink()

    def test_load_reference_stages_rejects_invalid_source(self):
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as tmp:
            tmp.write(
                "station,start_date,end_date,status,source,review_note\n"
                "MJ9,2020-01-01,2020-01-10,candidate,unknown_source,\n"
            )
            tmp_path = tmp.name

        try:
            with self.assertRaisesRegex(ValueError, "无效的阶段来源"):
                tangent_angle.load_reference_stages(tmp_path)
        finally:
            Path(tmp_path).unlink()


class ReferenceStageValidationTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.date_range("2020-01-01", periods=30)
        self.displacement = np.linspace(0, 100, len(self.dates))
        self.stations = {"MJ9": "MJ9/mm"}

    def _make_stage_df(self, status, station="MJ9",
                       start="2020-01-05", end="2020-01-15",
                       source="expert_manual"):
        return pd.DataFrame({
            "station": [station],
            "start_date": [start],
            "end_date": [end],
            "status": [status],
            "source": [source],
            "review_note": [""],
        })

    def test_no_approved_stage_returns_empty_dict(self):
        stages = self._make_stage_df("candidate")
        result = tangent_angle._build_manual_ranges_from_stages(
            stages, self.dates, self.stations
        )
        self.assertEqual(result, {})

    def test_single_approved_stage_in_training_period(self):
        stages = self._make_stage_df("approved")
        result = tangent_angle._build_manual_ranges_from_stages(
            stages, self.dates, self.stations, train_frac=1.0,
        )
        self.assertIn("MJ9", result)
        start, end = result["MJ9"]
        self.assertEqual(start, pd.Timestamp("2020-01-05"))
        self.assertEqual(end, pd.Timestamp("2020-01-15"))

    def test_approved_stage_beyond_training_period_raises(self):
        stages = self._make_stage_df(
            "approved", start="2020-01-20", end="2020-01-25"
        )
        with self.assertRaisesRegex(ValueError, "只能使用训练期数据"):
            tangent_angle._build_manual_ranges_from_stages(
                stages, self.dates, self.stations, train_frac=0.5,
            )

    def test_approved_stage_dates_not_in_data_raises(self):
        stages = self._make_stage_df(
            "approved", start="2019-12-31", end="2020-01-05"
        )
        with self.assertRaisesRegex(ValueError, "不在数据中"):
            tangent_angle._build_manual_ranges_from_stages(
                stages, self.dates, self.stations, train_frac=1.0,
            )

    def test_approved_stage_start_not_before_end_raises(self):
        stages = self._make_stage_df(
            "approved", start="2020-01-15", end="2020-01-05"
        )
        with self.assertRaisesRegex(ValueError, "起始必须早于结束"):
            tangent_angle._build_manual_ranges_from_stages(
                stages, self.dates, self.stations, train_frac=1.0,
            )

    def test_multiple_approved_stages_for_same_station_raises(self):
        stages = pd.DataFrame({
            "station": ["MJ9", "MJ9"],
            "start_date": ["2020-01-05", "2020-01-10"],
            "end_date": ["2020-01-10", "2020-01-15"],
            "status": ["approved", "approved"],
            "source": ["expert_manual", "expert_manual"],
            "review_note": ["", ""],
        })
        with self.assertRaisesRegex(ValueError, "多个已批准阶段"):
            tangent_angle._build_manual_ranges_from_stages(
                stages, self.dates, self.stations, train_frac=1.0,
            )

    def test_station_whitespace_cannot_bypass_duplicate_check(self):
        stages = pd.DataFrame({
            "station": ["MJ9", " MJ9 "],
            "start_date": ["2020-01-05", "2020-01-10"],
            "end_date": ["2020-01-10", "2020-01-15"],
            "status": ["approved", "approved"],
            "source": ["expert_manual", "expert_manual"],
        })

        with self.assertRaisesRegex(ValueError, "多个已批准阶段"):
            tangent_angle._build_manual_ranges_from_stages(
                stages, self.dates, self.stations, train_frac=1.0,
            )

    def test_unknown_approved_station_raises(self):
        stages = self._make_stage_df("approved", station="MJX")

        with self.assertRaisesRegex(ValueError, "未知测点"):
            tangent_angle._build_manual_ranges_from_stages(
                stages, self.dates, self.stations, train_frac=1.0,
            )

    def test_invalid_date_format_raises_clear_error(self):
        stages = pd.DataFrame({
            "station": ["MJ9"],
            "start_date": ["not-a-date"],
            "end_date": ["2020-01-10"],
            "status": ["approved"],
            "source": ["expert_manual"],
            "review_note": [""],
        })
        with self.assertRaisesRegex(ValueError, "日期无效"):
            tangent_angle._build_manual_ranges_from_stages(
                stages, self.dates, self.stations, train_frac=1.0,
            )


class ManualStageVeqTests(unittest.TestCase):
    def test_v_eq_only_uses_specified_stage(self):
        dates = pd.date_range("2020-01-01", periods=20)
        displacement = [0, 1, 3, 5, 7, 9, 11, 13, 15, 17,
                        20, 30, 50, 80, 120, 170, 231, 304, 390, 490]

        result = tangent_angle.estimate_uniform_rate(
            dates,
            displacement,
            manual_range=("2020-01-03", "2020-01-07"),
        )
        self.assertEqual(result["method"], "manual")
        self.assertAlmostEqual(result["v_eq_mm_per_day"], 2.0)
        self.assertEqual(result["start_date"], "2020-01-03")
        self.assertEqual(result["end_date"], "2020-01-07")
        self.assertEqual(result["n_rate_samples"], 4)

    def test_direct_manual_range_cannot_use_post_training_dates(self):
        dates = pd.date_range("2020-01-01", periods=40)
        displacement = np.arange(40, dtype=float)

        with self.assertRaisesRegex(ValueError, "只能使用训练期数据"):
            tangent_angle.estimate_uniform_rate(
                dates,
                displacement,
                train_frac=0.8,
                manual_range=("2020-02-02", "2020-02-05"),
            )


class NoManualConfigPreservesDefaultTests(unittest.TestCase):
    def test_no_reference_stages_produces_automatic_candidate(self):
        dates = pd.date_range("2020-01-01", periods=40)
        displacement = np.linspace(0, 39, len(dates))
        frame = pd.DataFrame({
            " Date ": dates,
            " MJ9/mm ": displacement,
        })

        result_default, params_default = tangent_angle.build_tangent_frame(
            frame,
            {"MJ9": "MJ9/mm"},
        )
        result_with_none, params_with_none = tangent_angle.build_tangent_frame(
            frame,
            {"MJ9": "MJ9/mm"},
            reference_stages=None,
        )

        self.assertEqual(
            params_default["MJ9"]["method"],
            params_with_none["MJ9"]["method"],
        )
        self.assertEqual(
            params_default["MJ9"]["v_eq_mm_per_day"],
            params_with_none["MJ9"]["v_eq_mm_per_day"],
        )
        self.assertEqual(params_default["MJ9"]["source"], "automatic_30d")

    def test_reference_stages_with_only_candidates_preserves_default(self):
        dates = pd.date_range("2020-01-01", periods=40)
        displacement = np.linspace(0, 39, len(dates))
        frame = pd.DataFrame({
            " Date ": dates,
            " MJ9/mm ": displacement,
        })
        candidates = pd.DataFrame({
            "station": ["MJ9"],
            "start_date": ["2020-01-05"],
            "end_date": ["2020-01-10"],
            "status": ["candidate"],
            "source": ["automatic_30d"],
            "review_note": [""],
        })

        result, params = tangent_angle.build_tangent_frame(
            frame,
            {"MJ9": "MJ9/mm"},
            reference_stages=candidates,
        )
        # No approved stage, so should use automatic candidate
        self.assertEqual(params["MJ9"]["method"], "automatic_candidate")


class BuildTangetFrameWithReferenceStagesTests(unittest.TestCase):
    def test_approved_stage_used_for_v_eq(self):
        dates = pd.date_range("2020-01-01", periods=40)
        # Steady displacement of 2 mm/day in early period
        displacement = np.arange(0, 80, 2, dtype=float)
        frame = pd.DataFrame({
            " Date ": dates,
            " MJ9/mm ": displacement,
        })
        approved = pd.DataFrame({
            "station": ["MJ9"],
            "start_date": ["2020-01-03"],
            "end_date": ["2020-01-08"],
            "status": ["approved"],
            "source": ["expert_manual"],
            "review_note": ["expert confirmed steady stage"],
        })

        result, params = tangent_angle.build_tangent_frame(
            frame,
            {"MJ9": "MJ9/mm"},
            reference_stages=approved,
        )
        self.assertEqual(params["MJ9"]["method"], "manual")
        self.assertAlmostEqual(params["MJ9"]["v_eq_mm_per_day"], 2.0)
        self.assertEqual(params["MJ9"]["start_date"], "2020-01-03")
        self.assertEqual(params["MJ9"]["end_date"], "2020-01-08")
        self.assertIn("source", params["MJ9"])
        self.assertEqual(params["MJ9"]["source"], "expert_manual")

    def test_approved_stage_exceeding_training_period_raises(self):
        dates = pd.date_range("2020-01-01", periods=40)
        displacement = np.arange(40, dtype=float)
        frame = pd.DataFrame({
            " Date ": dates,
            " MJ9/mm ": displacement,
        })
        # Training ends at index 32 (80% * 40), date 2020-02-01
        # 2020-02-02 is in test period
        approved = pd.DataFrame({
            "station": ["MJ9"],
            "start_date": ["2020-01-30"],
            "end_date": ["2020-02-05"],
            "status": ["approved"],
            "source": ["expert_manual"],
            "review_note": [""],
        })

        with self.assertRaisesRegex(ValueError, "只能使用训练期数据"):
            tangent_angle.build_tangent_frame(
                frame,
                {"MJ9": "MJ9/mm"},
                reference_stages=approved,
            )

    def test_multiple_approved_stages_raises(self):
        dates = pd.date_range("2020-01-01", periods=40)
        displacement = np.arange(40, dtype=float)
        frame = pd.DataFrame({
            " Date ": dates,
            " MJ9/mm ": displacement,
        })
        approved = pd.DataFrame({
            "station": ["MJ9", "MJ9"],
            "start_date": ["2020-01-03", "2020-01-10"],
            "end_date": ["2020-01-07", "2020-01-14"],
            "status": ["approved", "approved"],
            "source": ["expert_manual", "expert_manual"],
            "review_note": ["", ""],
        })

        with self.assertRaisesRegex(ValueError, "多个已批准阶段"):
            tangent_angle.build_tangent_frame(
                frame,
                {"MJ9": "MJ9/mm"},
                reference_stages=approved,
            )

    def test_manual_ranges_override_reference_stages(self):
        dates = pd.date_range("2020-01-01", periods=40)
        displacement = np.linspace(0, 39 * 3, 40)
        frame = pd.DataFrame({
            " Date ": dates,
            " MJ9/mm ": displacement,
        })
        approved = pd.DataFrame({
            "station": ["MJ9"],
            "start_date": ["2020-01-03"],
            "end_date": ["2020-01-08"],
            "status": ["approved"],
            "source": ["expert_manual"],
            "review_note": [""],
        })
        # Direct manual_ranges should take precedence
        result, params = tangent_angle.build_tangent_frame(
            frame,
            {"MJ9": "MJ9/mm"},
            manual_ranges={"MJ9": ("2020-01-05", "2020-01-10")},
            reference_stages=approved,
        )
        self.assertEqual(params["MJ9"]["method"], "manual")
        self.assertEqual(params["MJ9"]["start_date"], "2020-01-05")
        self.assertEqual(params["MJ9"]["end_date"], "2020-01-10")
        self.assertEqual(params["MJ9"]["source"], "manual_ranges_argument")


class FeaturePipelineReferenceStageTests(unittest.TestCase):
    def test_build_features_applies_approved_reference_stage(self):
        dates = pd.date_range("2020-01-01", periods=45)
        frame = pd.DataFrame({
            "Date": dates,
            "RWL/m": np.linspace(170, 175, len(dates)),
            "Rainfall/mm": np.zeros(len(dates)),
        })
        for index, column in enumerate(features.DISP_COLS, start=1):
            frame[column] = np.arange(len(dates), dtype=float) * index
        approved = pd.DataFrame({
            "station": [" MJ9 "],
            "start_date": ["2020-01-03"],
            "end_date": ["2020-01-08"],
            "status": [" approved "],
            "source": [" expert_manual "],
            "review_note": ["reviewed"],
        })

        _, parameters = features.build_features(
            frame,
            reference_stages=approved,
        )

        self.assertEqual(parameters["MJ9"]["method"], "manual")
        self.assertEqual(parameters["MJ9"]["source"], "expert_manual")
        self.assertEqual(parameters["MJ9"]["start_date"], "2020-01-03")


class StageFeasibilityTests(unittest.TestCase):
    def test_insufficient_rate_samples_raises(self):
        dates = pd.date_range("2020-01-01", periods=10)
        displacement = np.linspace(0, 9, 10)

        with self.assertRaisesRegex(ValueError, "速率样本不足"):
            tangent_angle._check_manual_range_feasibility(
                dates,
                displacement,
                ("2020-01-05", "2020-01-06"),
            )

    def test_nonpositive_v_eq_raises(self):
        dates = pd.date_range("2020-01-01", periods=10)
        displacement = np.zeros(10)

        with self.assertRaisesRegex(ValueError, "正的等速阶段速率"):
            tangent_angle._check_manual_range_feasibility(
                dates,
                displacement,
                ("2020-01-03", "2020-01-07"),
            )

    def test_valid_manual_range_passes_feasibility_check(self):
        dates = pd.date_range("2020-01-01", periods=10)
        displacement = np.arange(10, dtype=float)

        # Should not raise
        tangent_angle._check_manual_range_feasibility(
            dates,
            displacement,
            ("2020-01-03", "2020-01-07"),
        )


class ReviewOutputTests(unittest.TestCase):
    def test_review_figures_and_csv_exist(self):
        fig_dir = ROOT / "figures" / "tangent_angle" / "review"
        self.assertTrue(fig_dir.is_dir(), f"Missing directory: {fig_dir}")

        for station in ("MJ9", "MJ1", "MJ3"):
            fig_path = fig_dir / f"{station}_stage_review.png"
            self.assertTrue(fig_path.exists(), f"Missing figure: {fig_path}")

        csv_path = fig_dir / "candidate_stage_comparison.csv"
        self.assertTrue(csv_path.exists(), f"Missing CSV: {csv_path}")

    def test_candidate_csv_contains_required_columns(self):
        csv_path = ROOT / "figures" / "tangent_angle" / "review" / "candidate_stage_comparison.csv"
        df = pd.read_csv(csv_path)

        for col in ["station", "candidate_window_days", "method",
                     "source", "start_date", "end_date",
                     "stage_duration_days", "v_eq_mm_per_day",
                     "median_rate_mm_per_day", "alpha_green_days",
                     "alpha_agreement_rate_vs_30d",
                     "fusion_upgraded_days",
                     "fusion_agreement_rate_vs_30d",
                     "fusion_reason_alpha_watch_days"]:
            self.assertIn(col, df.columns, f"CSV missing column: {col}")

        self.assertEqual(set(df["station"]), {"MJ9", "MJ1", "MJ3"})
        self.assertEqual(set(df["candidate_window_days"]), {15, 30, 60})

    def test_candidate_csv_all_stages_are_candidates(self):
        csv_path = ROOT / "figures" / "tangent_angle" / "review" / "candidate_stage_comparison.csv"
        df = pd.read_csv(csv_path)
        valid = df["method"].isin(["automatic_candidate"]) | df["method"].isna()
        self.assertTrue(valid.all())

    def test_candidate_csv_no_post_training_access(self):
        """All candidate stages must be within the training period."""
        csv_path = ROOT / "figures" / "tangent_angle" / "review" / "candidate_stage_comparison.csv"
        df = pd.read_csv(csv_path)
        raw = pd.read_csv(ROOT / "data" / "monitoring_data.csv")
        raw.columns = [c.strip() for c in raw.columns]
        raw["Date"] = pd.to_datetime(raw["Date"])
        raw = raw.sort_values("Date").reset_index(drop=True)
        n_train = int(len(raw) * 0.8)
        train_end = raw["Date"].iloc[n_train - 1]

        for _, row in df.iterrows():
            if pd.isna(row["start_date"]):
                continue
            end_date = pd.Timestamp(row["end_date"])
            self.assertLessEqual(
                end_date,
                train_end,
                f"{row['station']} {row['candidate_window_days']}d stage "
                f"end_date {row['end_date']} exceeds training boundary "
                f"{train_end.strftime('%Y-%m-%d')}",
            )


class ConfigFileFormatTests(unittest.TestCase):
    def test_config_csv_no_approved_stages(self):
        """The initial config CSV must not contain any approved stages."""
        csv_path = ROOT / "config" / "tangent_reference_stages.csv"
        stages = tangent_angle.load_reference_stages(csv_path)
        approved = stages[stages["status"].str.strip() == "approved"]
        self.assertEqual(
            len(approved),
            0,
            "Claude must not approve any stages automatically",
        )

    def test_config_csv_all_entries_are_candidates(self):
        csv_path = ROOT / "config" / "tangent_reference_stages.csv"
        stages = tangent_angle.load_reference_stages(csv_path)
        self.assertTrue(
            stages["status"].str.strip().eq("candidate").all(),
            "All initial entries must be 'candidate'",
        )

    def test_config_csv_source_distinguishes_windows(self):
        csv_path = ROOT / "config" / "tangent_reference_stages.csv"
        stages = tangent_angle.load_reference_stages(csv_path)
        sources = set(stages["source"].str.strip())
        self.assertTrue(
            sources.issubset(
                {"automatic_15d", "automatic_30d", "automatic_60d",
                 "expert_manual"}
            ),
            f"Unexpected source values: {sources}",
        )
        self.assertIn("automatic_15d", sources)
        self.assertIn("automatic_30d", sources)
        self.assertIn("automatic_60d", sources)


if __name__ == "__main__":
    unittest.main()
