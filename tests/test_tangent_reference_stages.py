"""Regression tests for the current reference-stage interface."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from features import build_features as features  # noqa: E402
from features import tangent_angle  # noqa: E402


class ReferenceStageLoadingTests(unittest.TestCase):
    def _load_csv(self, text: str) -> pd.DataFrame:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference_stages.csv"
            path.write_text(text, encoding="utf-8")
            return tangent_angle.load_reference_stages(path)

    def test_load_reference_stages_accepts_valid_csv(self):
        stages = self._load_csv(
            "station,start_date,end_date,status,source,review_note\n"
            "MJ9,2020-01-01,2020-01-10,candidate,automatic_30d,\n"
        )

        self.assertTrue(
            {
                "station",
                "start_date",
                "end_date",
                "status",
                "source",
            }.issubset(stages.columns)
        )
        self.assertEqual(stages.loc[0, "station"], "MJ9")
        self.assertEqual(stages.loc[0, "status"], "candidate")
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(stages["start_date"]))

    def test_load_reference_stages_rejects_missing_required_columns(self):
        with self.assertRaisesRegex(ValueError, "缺少必需列"):
            self._load_csv("station,start_date\nMJ9,2020-01-01\n")

    def test_load_reference_stages_rejects_invalid_status(self):
        with self.assertRaisesRegex(ValueError, "无效的阶段状态"):
            self._load_csv(
                "station,start_date,end_date,status,source\n"
                "MJ9,2020-01-01,2020-01-10,invalid,automatic_15d\n"
            )

    def test_load_reference_stages_rejects_invalid_source(self):
        with self.assertRaisesRegex(ValueError, "无效的阶段来源"):
            self._load_csv(
                "station,start_date,end_date,status,source\n"
                "MJ9,2020-01-01,2020-01-10,candidate,unknown_source\n"
            )


class ReferenceStageValidationTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.date_range("2020-01-01", periods=40)
        self.stations = {"MJ9": "MJ9/mm"}

    def _stage(
        self,
        status: str,
        *,
        station: str = "MJ9",
        start: str = "2020-01-03",
        end: str = "2020-01-08",
        source: str = "expert_manual",
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "station": [station],
                "start_date": [start],
                "end_date": [end],
                "status": [status],
                "source": [source],
                "review_note": [""],
            }
        )

    def test_approved_stage_is_limited_to_training_dates(self):
        stages = self._stage(
            "approved",
            start="2020-01-30",
            end="2020-02-05",
        )

        with self.assertRaisesRegex(ValueError, "只能使用训练期数据"):
            tangent_angle._build_manual_ranges_from_stages(
                stages,
                self.dates,
                self.stations,
                train_frac=0.8,
            )

    def test_approved_stage_dates_must_exist_in_data(self):
        stages = self._stage("approved", start="2019-12-31")

        with self.assertRaisesRegex(ValueError, "不在数据中"):
            tangent_angle._build_manual_ranges_from_stages(
                stages,
                self.dates,
                self.stations,
                train_frac=1.0,
            )

    def test_invalid_stage_date_is_rejected(self):
        stages = self._stage("approved", start="not-a-date")

        with self.assertRaisesRegex(ValueError, "日期无效"):
            tangent_angle._build_manual_ranges_from_stages(
                stages,
                self.dates,
                self.stations,
                train_frac=1.0,
            )

    def test_approved_stage_rejects_start_not_before_end(self):
        for start, end in (
            ("2020-01-08", "2020-01-08"),
            ("2020-01-09", "2020-01-08"),
        ):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ValueError, "起始必须早于结束"):
                    tangent_angle._build_manual_ranges_from_stages(
                        self._stage("approved", start=start, end=end),
                        self.dates,
                        self.stations,
                        train_frac=1.0,
                    )

    def test_duplicate_approved_station_rejects_whitespace_variant(self):
        stages = pd.DataFrame(
            {
                "station": ["MJ9", " MJ9 "],
                "start_date": ["2020-01-03", "2020-01-10"],
                "end_date": ["2020-01-08", "2020-01-15"],
                "status": ["approved", "approved"],
                "source": ["expert_manual", "expert_manual"],
            }
        )

        with self.assertRaisesRegex(ValueError, "多个已批准阶段"):
            tangent_angle._build_manual_ranges_from_stages(
                stages,
                self.dates,
                self.stations,
                train_frac=1.0,
            )

    def test_unknown_approved_station_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知测点"):
            tangent_angle._build_manual_ranges_from_stages(
                self._stage("approved", station="MJX"),
                self.dates,
                self.stations,
                train_frac=1.0,
            )


class ReferenceStageApplicationTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.date_range("2020-01-01", periods=40)
        self.frame = pd.DataFrame(
            {
                " Date ": self.dates,
                " MJ9/mm ": np.arange(40, dtype=float),
            }
        )
        self.stations = {"MJ9": "MJ9/mm"}

    def test_none_and_candidate_reference_stages_preserve_automatic_default(self):
        _, default_parameters = tangent_angle.build_tangent_frame(
            self.frame,
            self.stations,
        )
        _, none_parameters = tangent_angle.build_tangent_frame(
            self.frame,
            self.stations,
            reference_stages=None,
        )
        candidates = pd.DataFrame(
            {
                "station": ["MJ9"],
                "start_date": ["2020-01-03"],
                "end_date": ["2020-01-08"],
                "status": ["candidate"],
                "source": ["automatic_30d"],
            }
        )
        _, candidate_parameters = tangent_angle.build_tangent_frame(
            self.frame,
            self.stations,
            reference_stages=candidates,
        )

        for parameters in (none_parameters, candidate_parameters):
            self.assertEqual(parameters["MJ9"]["method"], "automatic_candidate")
            self.assertEqual(parameters["MJ9"]["source"], "automatic_30d")
        self.assertEqual(
            default_parameters["MJ9"]["v_eq_mm_per_day"],
            none_parameters["MJ9"]["v_eq_mm_per_day"],
        )
        self.assertEqual(
            default_parameters["MJ9"]["v_eq_mm_per_day"],
            candidate_parameters["MJ9"]["v_eq_mm_per_day"],
        )

    def test_approved_stage_reaches_build_tangent_frame(self):
        displacement = np.r_[
            np.arange(10, dtype=float) * 3.0,
            27.0 + np.arange(30, dtype=float),
        ]
        frame = self.frame.copy()
        frame[" MJ9/mm "] = displacement
        stages = pd.DataFrame(
            {
                "station": [" MJ9 "],
                "start_date": ["2020-01-03"],
                "end_date": ["2020-01-08"],
                "status": [" approved "],
                "source": [" expert_manual "],
                "review_note": ["reviewed"],
            }
        )

        _, parameters = tangent_angle.build_tangent_frame(
            frame,
            self.stations,
            reference_stages=stages,
        )

        _, automatic_parameters = tangent_angle.build_tangent_frame(
            frame,
            self.stations,
        )

        self.assertEqual(parameters["MJ9"]["method"], "manual")
        self.assertEqual(parameters["MJ9"]["source"], "expert_manual")
        self.assertEqual(parameters["MJ9"]["start_date"], "2020-01-03")
        self.assertEqual(parameters["MJ9"]["end_date"], "2020-01-08")
        self.assertAlmostEqual(parameters["MJ9"]["v_eq_mm_per_day"], 3.0)
        self.assertNotAlmostEqual(
            parameters["MJ9"]["v_eq_mm_per_day"],
            automatic_parameters["MJ9"]["v_eq_mm_per_day"],
        )

    def test_manual_ranges_take_precedence_over_approved_reference_stage(self):
        stages = self._approved_stage()

        _, parameters = tangent_angle.build_tangent_frame(
            self.frame,
            self.stations,
            manual_ranges={"MJ9": ("2020-01-05", "2020-01-10")},
            reference_stages=stages,
        )

        self.assertEqual(parameters["MJ9"]["method"], "manual")
        self.assertEqual(parameters["MJ9"]["source"], "manual_ranges_argument")
        self.assertEqual(parameters["MJ9"]["start_date"], "2020-01-05")
        self.assertEqual(parameters["MJ9"]["end_date"], "2020-01-10")

    def test_approved_stage_reaches_build_features(self):
        dates = pd.date_range("2020-01-01", periods=45)
        frame = pd.DataFrame(
            {
                "Date": dates,
                "RWL/m": np.linspace(170, 175, len(dates)),
                "Rainfall/mm": np.zeros(len(dates)),
            }
        )
        for index, column in enumerate(features.DISP_COLS, start=1):
            frame[column] = np.arange(len(dates), dtype=float) * index
        stages = self._approved_stage()

        output, parameters = features.build_features(
            frame,
            reference_stages=stages,
        )

        self.assertGreater(len(output), 0)
        self.assertEqual(parameters["MJ9"]["method"], "manual")
        self.assertEqual(parameters["MJ9"]["source"], "expert_manual")

    def _approved_stage(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "station": ["MJ9"],
                "start_date": ["2020-01-03"],
                "end_date": ["2020-01-08"],
                "status": ["approved"],
                "source": ["expert_manual"],
            }
        )


if __name__ == "__main__":
    unittest.main()
