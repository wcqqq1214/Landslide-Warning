import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

from convlstm import elevation_diagnostic_protocol as protocol
from convlstm import rolling_validation as rolling


class ConvLSTMRollingValidationTests(unittest.TestCase):
    def test_outputs_are_isolated_in_versioned_stage_bundle(self):
        expected = (
            ROOT
            / "figures"
            / "convlstm"
            / "runs"
            / "displacement_elevation_exog_v1"
            / "fixed120_v1"
            / "rolling_seed0"
        )

        self.assertEqual(rolling.OUT_FOLDS.parent, expected)
        self.assertEqual(rolling.OUT_METRICS.parent, expected)
        self.assertEqual(rolling.OUT_PREDICTIONS.parent, expected)
        self.assertEqual(rolling.OUT_MANIFEST.parent, expected)

    def test_frozen_protocol_matches_current_seven_channel_model(self):
        frozen = protocol.load_and_validate_protocol()

        self.assertEqual(
            frozen["model_input"]["schema"],
            "displacement_elevation_exog_v1",
        )
        self.assertEqual(frozen["model_input"]["channel_count"], 7)
        self.assertEqual(frozen["training"]["epochs"], 120)
        self.assertFalse(frozen["outer_validation"]["test_used_for_selection"])

    def test_protocol_is_predeclared(self):
        self.assertEqual(rolling.N_SPLITS, 3)
        self.assertEqual(rolling.TEST_WINDOWS, 287)
        self.assertEqual(rolling.MIN_FIT_WINDOWS, 365)
        self.assertEqual(rolling.VALIDATION_SEED, 0)

    def test_default_splits_expand_and_keep_tests_non_overlapping(self):
        splits = rolling.expanding_window_splits(1432)

        self.assertEqual([split.train_windows for split in splits], [564, 851, 1138])
        self.assertEqual([split.fit_windows for split in splits], [452, 681, 911])
        self.assertEqual(
            [split.calibration_windows for split in splits],
            [112, 170, 227],
        )
        self.assertTrue(all(split.test_windows == 287 for split in splits))
        for earlier, later in zip(splits, splits[1:]):
            self.assertEqual(earlier.test_stop_index, later.split_index)
            self.assertLess(earlier.train_windows, later.train_windows)

    def test_split_metadata_enforces_fit_calibration_test_order(self):
        dates = pd.date_range("2016-07-30", periods=1432)
        metadata = [
            rolling.split_metadata(split, dates)
            for split in rolling.expanding_window_splits(len(dates))
        ]

        self.assertEqual(metadata[0]["fit_start_date"], "2016-08-06")
        self.assertEqual(metadata[0]["test_start_date"], "2018-02-21")
        self.assertEqual(metadata[0]["test_end_date"], "2018-12-04")
        self.assertEqual(metadata[-1]["test_start_date"], "2019-09-18")
        self.assertEqual(metadata[-1]["test_end_date"], "2020-06-30")
        self.assertTrue(
            all(not row["test_length_selected_from_results"] for row in metadata)
        )
        self.assertTrue(
            all(not row["confirmatory_external_validation"] for row in metadata)
        )

    def test_split_plan_rejects_insufficient_or_non_integer_inputs(self):
        with self.assertRaises(ValueError):
            rolling.expanding_window_splits(100)
        with self.assertRaises(ValueError):
            rolling.expanding_window_splits(1000, min_fit_windows=900)
        with self.assertRaises(TypeError):
            rolling.expanding_window_splits(1432.0)

    def test_metric_rows_report_every_scope_and_interval_variant(self):
        actual = np.arange(8, dtype=float).reshape(4, 2)
        result = {
            "raw_p10": actual - 0.4,
            "p50": actual + 0.1,
            "raw_p90": actual + 0.4,
            "calibrated_p10": actual - 0.5,
            "calibrated_p90": actual + 0.5,
            "actual": actual,
            "persistence": actual - 1.0,
            "qhat": np.array([0.1, 0.1]),
        }
        metadata = {
            "fold": 1,
            "test_start_date": "2020-01-01",
            "test_end_date": "2020-01-04",
            "test_windows": 4,
        }

        rows = rolling.metric_rows(result, ["A", "B"], metadata)

        self.assertEqual(len(rows), 6)
        self.assertEqual({row["scope"] for row in rows}, {"overall", "A", "B"})
        self.assertEqual(
            {row["interval_variant"] for row in rows},
            {"raw", "calibrated"},
        )
        self.assertTrue(all(row["n_dates"] == 4 for row in rows))
        overall = next(row for row in rows if row["scope"] == "overall")
        self.assertAlmostEqual(overall["model_mean_error"], 0.1)
        self.assertAlmostEqual(overall["baseline_mean_error"], -1.0)
        self.assertAlmostEqual(overall["mean_actual_increment"], 1.0)
        self.assertAlmostEqual(overall["mean_predicted_increment"], 1.1)

    def test_prediction_rows_preserve_date_station_pairing(self):
        actual = np.arange(8, dtype=float).reshape(4, 2)
        result = {
            "raw_p10": actual - 0.4,
            "p50": actual,
            "raw_p90": actual + 0.4,
            "calibrated_p10": actual - 0.5,
            "calibrated_p90": actual + 0.5,
            "actual": actual,
            "persistence": actual - 1.0,
            "qhat": np.array([0.1, 0.1]),
        }
        dates = pd.date_range("2020-01-01", periods=4)

        rows = rolling.prediction_rows(result, ["A", "B"], dates, fold=2)

        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["date"], "2020-01-01")
        self.assertEqual(rows[0]["station"], "A")
        self.assertEqual(rows[1]["station"], "B")
        self.assertEqual(rows[-1]["date"], "2020-01-04")
        self.assertTrue(all(row["fold"] == 2 for row in rows))
        self.assertTrue(
            all(
                row["model_input_schema"]
                == "displacement_elevation_exog_v1"
                for row in rows
            )
        )
        self.assertTrue(all(row["model_input_channels"] == 7 for row in rows))

    def test_stage_bundle_failure_preserves_previous_complete_bundle(self):
        frames = {
            "first.csv": pd.DataFrame({"value": [1]}),
            "second.csv": pd.DataFrame({"value": [2]}),
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "complete"
            target.mkdir()
            sentinel = target / "sentinel.txt"
            sentinel.write_text("old-complete", encoding="utf-8")
            real_writer = protocol._write_dataframe_csv
            calls = 0

            def fail_on_second(frame, path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated bundle write failure")
                return real_writer(frame, path)

            with mock.patch.object(
                protocol,
                "_write_dataframe_csv",
                side_effect=fail_on_second,
            ), self.assertRaises(OSError):
                protocol.write_stage_bundle(
                    target,
                    frames,
                    stage="test",
                    stage_parameters={},
                    source_paths=[],
                )

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "old-complete")
            self.assertEqual(list(Path(tmp_dir).glob(".complete.staging-*")), [])

    def test_stage_bundle_manifest_binds_inputs_sources_and_output_hashes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "complete"
            upstream = Path(tmp_dir) / "upstream.csv"
            upstream.write_text("upstream\n", encoding="utf-8")
            manifest_path = protocol.write_stage_bundle(
                target,
                {"result.csv": pd.DataFrame({"value": [1, 2]})},
                stage="test",
                stage_parameters={"seed": 0},
                source_paths=[Path(protocol.__file__)],
                additional_input_paths=[upstream],
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            protocol.validate_stage_manifest(
                manifest_path,
                expected_stage="test",
                required_outputs=("result.csv",),
            )

            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["model_input"]["channel_count"], 7)
            self.assertEqual(manifest["outputs"][0]["rows"], 2)
            self.assertEqual(
                manifest["outputs"][0]["path"],
                str((target / "result.csv").resolve()),
            )
            self.assertEqual(len(manifest["outputs"][0]["sha256"]), 64)
            self.assertEqual(len(manifest["inputs"]), 4)
            self.assertEqual(manifest["inputs"][-1]["path"], str(upstream.resolve()))
            self.assertEqual(
                manifest["inputs"][-1]["sha256"],
                protocol.file_sha256(upstream),
            )
            self.assertEqual(len(manifest["sources"]), 1)
            manifest["sources"][0]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                protocol.validate_stage_manifest(
                    manifest_path,
                    expected_stage="test",
                    required_outputs=("result.csv",),
                )

    def test_recovery_chooses_newest_valid_backup_not_uuid_order(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            parent = Path(tmp_dir)
            target = parent / "bundle"
            older = parent / ".bundle.backup-zzzz"
            newer = parent / ".bundle.backup-aaaa"
            corrupted = parent / ".bundle.backup-corrupted"
            for backup, value in ((older, "older"), (newer, "newer")):
                backup.mkdir()
                value_path = backup / "value.txt"
                value_path.write_text(value, encoding="utf-8")
                (backup / protocol.MANIFEST_NAME).write_text(
                    json.dumps({
                        "status": "completed",
                        "outputs": [{
                            "path": "bundle/value.txt",
                            "sha256": protocol.file_sha256(value_path),
                        }],
                    }),
                    encoding="utf-8",
                )
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            corrupted.mkdir()
            (corrupted / "value.txt").write_text("corrupted", encoding="utf-8")
            (corrupted / protocol.MANIFEST_NAME).write_text(
                json.dumps({
                    "status": "completed",
                    "outputs": [{
                        "path": "bundle/value.txt",
                        "sha256": "0" * 64,
                    }],
                }),
                encoding="utf-8",
            )
            os.utime(corrupted, (3, 3))

            protocol._recover_interrupted_promotion(target)

            self.assertEqual(
                (target / "value.txt").read_text(encoding="utf-8"),
                "newer",
            )
            self.assertFalse(older.exists())

    def test_output_contract_rejects_cross_fold_date_overlap(self):
        folds = pd.DataFrame({
            "fold": [1, 2],
            "test_windows": [1, 1],
        })
        metrics = pd.DataFrame([
            {
                "fold": fold,
                "scope": scope,
                "interval_variant": variant,
                "model_rmse": 1.0,
            }
            for fold in (1, 2)
            for variant in ("raw", "calibrated")
            for scope in ("overall", "A", "B")
        ])
        predictions = pd.DataFrame([
            {
                "fold": fold,
                "date": "2020-01-01",
                "station": station,
                "actual": 1.0,
                "persistence": 0.0,
                "raw_p10": 0.5,
                "p50": 1.0,
                "raw_p90": 1.5,
                "calibrated_p10": 0.4,
                "calibrated_p90": 1.6,
                "qhat_mm": 0.1,
            }
            for fold in (1, 2)
            for station in ("A", "B")
        ])

        with self.assertRaises(RuntimeError):
            rolling.validate_output_frames(
                folds,
                metrics,
                predictions,
                ["A", "B"],
            )


if __name__ == "__main__":
    unittest.main()
