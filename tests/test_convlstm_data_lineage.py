"""Regression checks for the read-only Outang data-lineage audit."""

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "code"
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

from convlstm import data_lineage_audit as lineage


class ConvLSTMDataLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = lineage.load_monitoring_csv(lineage.SOURCE_CSV)
        cls.monthly, cls.summary = lineage.temporal_polynomial_fingerprint(
            cls.frame
        )

    def test_repository_csv_matches_the_published_figshare_workbook(self):
        audit = lineage.audit_source_pair(
            lineage.SOURCE_CSV,
            lineage.SOURCE_XLSX,
        )

        self.assertEqual(
            audit["xlsx_md5"],
            "372d1608f46d7fcdb9805568d1c0782a",
        )
        self.assertTrue(audit["xlsx_matches_figshare_release"])
        self.assertEqual(audit["csv_shape"], [1461, 17])
        self.assertEqual(audit["xlsx_shape"], [1461, 17])
        self.assertTrue(audit["columns_equal"])
        self.assertTrue(audit["dates_equal"])
        self.assertTrue(audit["numeric_values_equal_within_tolerance"])
        self.assertEqual(audit["xlsx_structure"]["formula_cell_count"], 0)
        self.assertEqual(audit["xlsx_structure"]["hidden_sheet_count"], 0)

    def test_monthly_cubic_fingerprint_separates_targets_from_controls(self):
        targets = self.summary.loc[
            self.summary["role"].eq("fingerprint_target")
        ]
        controls = self.summary.loc[
            self.summary["role"].eq("negative_control")
        ]

        self.assertEqual(set(targets["column"]), set(lineage.FINGERPRINT_COLS))
        self.assertTrue(targets["all_months_pass_cubic_fingerprint"].all())
        self.assertTrue(
            targets["months_passing_cubic_fingerprint"].eq(48).all()
        )
        self.assertFalse(
            controls["all_months_pass_cubic_fingerprint"].any()
        )
        self.assertTrue(
            controls["months_passing_cubic_fingerprint"].eq(0).all()
        )

    def test_high_precision_breaks_are_confined_to_month_boundaries(self):
        high_precision = self.summary.loc[
            self.summary["column"].isin(lineage.HIGH_PRECISION_COLS)
        ]

        self.assertTrue(
            high_precision["within_month_d4_exceedances"].eq(0).all()
        )
        self.assertTrue(
            high_precision["cross_month_d4_exceedances"].eq(141).all()
        )
        self.assertTrue(
            high_precision["all_d4_exceedances_cross_month"].all()
        )
        self.assertTrue(
            high_precision["cross_month_exceedance_endpoint_days"]
            .eq("2,3,4")
            .all()
        )

    def test_monthly_cubic_segments_cross_all_model_boundaries(self):
        boundaries = lineage.audit_split_boundary_months(self.frame)
        row_counts = {
            boundary: (
                int(group["rows_before_boundary_in_month"].iloc[0]),
                int(group["rows_on_or_after_boundary_in_month"].iloc[0]),
            )
            for boundary, group in boundaries.groupby("boundary")
        }

        self.assertEqual(row_counts["first_model_target"], (5, 26))
        self.assertEqual(row_counts["fit_to_calibration"], (2, 26))
        self.assertEqual(row_counts["calibration_to_test"], (17, 13))
        self.assertTrue(
            boundaries["same_monthly_cubic_segment_across_boundary"].all()
        )

    def test_cross_boundary_predictability_is_labeled_as_diagnostic(self):
        audit = lineage.audit_split_cross_boundary_predictability(self.frame)
        protocols = {
            boundary: (
                group["direction"].iloc[0],
                int(group["fit_rows"].iloc[0]),
                int(group["evaluation_rows"].iloc[0]),
            )
            for boundary, group in audit.groupby("boundary")
        }
        high_precision = audit.loc[
            audit["precision_group"].eq("high_precision")
        ]
        september_rounded = audit.loc[
            audit["boundary"].eq("calibration_to_test")
            & audit["precision_group"].eq("five_decimal_rounded")
        ]

        self.assertEqual(
            protocols["first_model_target"],
            ("pre_boundary_to_on_or_after_boundary", 5, 26),
        )
        self.assertEqual(
            protocols["fit_to_calibration"],
            ("on_or_after_boundary_to_pre_boundary_backcast", 26, 2),
        )
        self.assertEqual(
            protocols["calibration_to_test"],
            ("pre_boundary_to_on_or_after_boundary", 17, 13),
        )
        self.assertLess(high_precision["max_absolute_error"].max(), 1e-8)
        self.assertLess(
            september_rounded["max_absolute_error"].max(),
            1.1e-4,
        )
        self.assertEqual(
            set(audit["interpretation"]),
            {"algebraic_dependence_diagnostic_not_forecast_evaluation"},
        )

    def test_prediction_rows_align_with_dated_actual_and_persistence(self):
        audit, splits = lineage.audit_prediction_date_alignment(
            lineage.FEATURE_CSV,
            lineage.PREDICTION_CSV,
        )

        self.assertTrue(audit["alignment_passed"])
        self.assertTrue(audit["frozen_split_schedule_passed"])
        self.assertTrue(audit["station_set_matches"])
        self.assertEqual(audit["missing_expected_date_station_split_keys"], 0)
        self.assertEqual(audit["unexpected_date_station_split_keys"], 0)
        self.assertEqual(audit["duplicate_date_station_rows"], 0)
        self.assertEqual(audit["max_abs_actual_alignment_error"], 0)
        self.assertEqual(audit["max_abs_persistence_alignment_error"], 0)
        self.assertEqual(
            dict(zip(splits["split"], splits["n_dates"])),
            {"fit": 911, "calibration": 227, "test": 287},
        )

    def test_prediction_alignment_rejects_an_incomplete_schedule(self):
        predictions = pd.read_csv(lineage.PREDICTION_CSV).iloc[:-1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "predictions.csv"
            predictions.to_csv(path, index=False)
            audit, _ = lineage.audit_prediction_date_alignment(
                lineage.FEATURE_CSV,
                path,
            )

        self.assertFalse(audit["alignment_passed"])
        self.assertFalse(audit["frozen_split_schedule_passed"])
        self.assertEqual(audit["missing_expected_date_station_split_keys"], 1)

    def test_prediction_alignment_rejects_a_wrong_split_label(self):
        predictions = pd.read_csv(lineage.PREDICTION_CSV)
        predictions.loc[0, "split"] = "test"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "predictions.csv"
            predictions.to_csv(path, index=False)
            audit, _ = lineage.audit_prediction_date_alignment(
                lineage.FEATURE_CSV,
                path,
            )

        self.assertFalse(audit["alignment_passed"])
        self.assertFalse(audit["frozen_split_schedule_passed"])
        self.assertEqual(audit["missing_expected_date_station_split_keys"], 1)
        self.assertEqual(audit["unexpected_date_station_split_keys"], 1)

    def test_prediction_alignment_rejects_non_finite_values(self):
        predictions = pd.read_csv(lineage.PREDICTION_CSV)
        predictions.loc[0, "actual"] = float("nan")
        predictions.loc[1, "persistence"] = float("inf")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "predictions.csv"
            predictions.to_csv(path, index=False)
            audit, _ = lineage.audit_prediction_date_alignment(
                lineage.FEATURE_CSV,
                path,
            )

        self.assertFalse(audit["alignment_passed"])
        self.assertFalse(audit["prediction_values_finite"])
        self.assertEqual(audit["non_finite_actual_values"], 1)
        self.assertEqual(audit["non_finite_persistence_values"], 1)
        self.assertIsNone(audit["max_abs_actual_alignment_error"])
        self.assertIsNone(audit["max_abs_persistence_alignment_error"])

    def test_prediction_alignment_uses_null_for_all_non_finite_values(self):
        predictions = pd.read_csv(lineage.PREDICTION_CSV)
        predictions["actual"] = float("nan")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "predictions.csv"
            predictions.to_csv(path, index=False)
            audit, splits = lineage.audit_prediction_date_alignment(
                lineage.FEATURE_CSV,
                path,
            )

        self.assertFalse(audit["alignment_passed"])
        self.assertEqual(
            audit["non_finite_actual_values"],
            len(predictions),
        )
        self.assertIsNone(audit["max_abs_actual_alignment_error"])
        self.assertTrue(
            splits["max_abs_actual_alignment_error"].isna().all()
        )

    def test_manifest_is_deterministic_across_repeated_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            first = lineage.run_audit(output_dir=output_dir)
            manifest_path = (
                output_dir / "ootang_data_lineage_manifest.json"
            )
            first_bytes = manifest_path.read_bytes()
            second = lineage.run_audit(output_dir=output_dir)
            second_bytes = manifest_path.read_bytes()

        self.assertEqual(first, second)
        self.assertEqual(first_bytes, second_bytes)
        self.assertNotIn("repository_state", first)
        self.assertIn("audit_implementation", first)

    def test_manifest_separates_prototype_and_confirmatory_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = lineage.run_audit(output_dir=Path(directory))

        self.assertEqual(
            manifest["source_recovery_status"],
            "unavailable_by_project_constraint",
        )
        self.assertEqual(manifest["prototype_run_gate"]["status"], "allowed")
        self.assertEqual(
            manifest["confirmatory_evidence_gate"]["status"],
            "blocked",
        )
        self.assertFalse(manifest["formal_warning_output"])


if __name__ == "__main__":
    unittest.main()
