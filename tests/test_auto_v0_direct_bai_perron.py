"""Contracts for the automatic fit-only V0 candidate diagnostic."""

from __future__ import annotations

from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
import sys

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import auto_v0_direct_bai_perron as auto_v0  # noqa: E402
from warning.draft_evidence import OOTANG_STATIONS  # noqa: E402


class AutoV0ProfileTests(unittest.TestCase):
    def test_profile_locks_automatic_fit_only_scope(self):
        profile = auto_v0.load_auto_v0_profile()
        self.assertEqual(profile["case"], "ootang")
        self.assertFalse(profile["formal_warning_output"])
        self.assertTrue(profile["candidate_v0_only"])
        self.assertTrue(profile["input"]["fit_only"])
        self.assertTrue(profile["input"]["no_manual_stage_ranges"])
        self.assertTrue(profile["input"]["no_calibration_or_test_selection"])
        self.assertFalse(profile["segmentation"]["mvif_prerequisite"])
        self.assertFalse(profile["segmentation"]["kmeans_fallback"])
        self.assertEqual(
            profile["segmentation"]["segment_statistics_origin"],
            "per_segment_start",
        )
        self.assertEqual(profile["segmentation"]["sse_roundoff_multiplier"], 32.0)
        self.assertEqual(
            profile["v0"]["status_values"],
            ["initial_segment_selected", "stable_full_fit_baseline", "unavailable"],
        )

    def test_profile_rejects_manual_or_kmeans_drift(self):
        profile = auto_v0.load_auto_v0_profile()
        for field, value in (
            ("no_manual_stage_ranges", False),
            ("mvif_prerequisite", True),
            ("kmeans_fallback", True),
        ):
            changed = json.loads(json.dumps(profile))
            if field == "no_manual_stage_ranges":
                changed["input"][field] = value
            else:
                changed["segmentation"][field] = value
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profile.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(auto_v0.AutoV0ProfileError):
                    auto_v0.load_auto_v0_profile(path)


class AutoV0SyntheticTests(unittest.TestCase):
    @staticmethod
    def _frame(values):
        dates = pd.date_range("2016-01-01", periods=len(values), freq="D")
        displacement = pd.Series(values, dtype=float)
        velocity = displacement.diff()
        return pd.DataFrame(
            {
                "case": "ootang",
                "date": dates,
                "station": "ATU1",
                "displacement": displacement,
                "displacement_valid": True,
                "velocity": velocity,
                "velocity_status": np.where(velocity.notna(), "valid", "warmup"),
            }
        )

    def test_piecewise_series_selects_initial_segment_and_v0(self):
        first = np.arange(60, dtype=float) * 0.1
        second = first[-1] + np.arange(60, dtype=float) * 0.5
        frame = self._frame(1e6 + np.concatenate((first, second)))
        candidate, segments = auto_v0.select_direct_candidate(
            frame,
            station="ATU1",
            fit_end_date=frame["date"].iloc[-1],
            profile=auto_v0.load_auto_v0_profile(),
        )
        self.assertEqual(candidate["selection_status"], "initial_segment_selected")
        self.assertGreater(candidate["following_segment_slope_mm_per_day"], candidate["candidate_v_mm_per_day"])
        self.assertGreater(candidate["candidate_v0_mm_per_day"], 0.0)
        self.assertGreaterEqual(len(segments), 2)
        time_days = (
            frame["date"] - frame["date"].iloc[0]
        ).dt.total_seconds().to_numpy(dtype=float) / 86_400.0
        for segment in segments.to_dict(orient="records"):
            selected = frame["date"].between(
                segment["segment_start_date"], segment["segment_end_date"]
            )
            design = np.column_stack(
                (np.ones(int(selected.sum())), time_days[selected])
            )
            values = frame.loc[selected, "displacement"].to_numpy(dtype=float)
            expected, _, _, _ = np.linalg.lstsq(design, values, rcond=None)
            residual_sse = float(np.sum((values - design @ expected) ** 2))
            np.testing.assert_allclose(segment["intercept"], expected[0], atol=1e-8)
            np.testing.assert_allclose(
                segment["slope_mm_per_day"], expected[1], atol=1e-10
            )
            np.testing.assert_allclose(segment["sse"], residual_sse, atol=1e-8)

    def test_single_positive_segment_is_explicit_baseline(self):
        values = np.arange(100, dtype=float) * 0.2
        values += np.random.default_rng(7).normal(0.0, 0.1, len(values))
        frame = self._frame(values)
        candidate, segments = auto_v0.select_direct_candidate(
            frame,
            station="ATU1",
            fit_end_date=frame["date"].iloc[-1],
            profile=auto_v0.load_auto_v0_profile(),
        )
        self.assertEqual(candidate["selection_status"], "stable_full_fit_baseline")
        self.assertIsNone(candidate["first_break_date"])
        self.assertEqual(len(segments), 1)
        np.testing.assert_allclose(candidate["candidate_v_mm_per_day"], 0.2, atol=1e-3)

    def test_negative_series_is_unavailable(self):
        frame = self._frame(-np.arange(100, dtype=float) * 0.2)
        candidate, _ = auto_v0.select_direct_candidate(
            frame,
            station="ATU1",
            fit_end_date=frame["date"].iloc[-1],
            profile=auto_v0.load_auto_v0_profile(),
        )
        self.assertEqual(candidate["selection_status"], "unavailable")
        self.assertEqual(candidate["failure_reason"], "nonpositive_initial_segment_slope")
        self.assertTrue(pd.isna(candidate["candidate_v_mm_per_day"]))
        self.assertTrue(pd.isna(candidate["selected_velocity_sigma_mm_per_day"]))
        self.assertTrue(pd.isna(candidate["candidate_v0_mm_per_day"]))

    def test_short_series_is_unavailable(self):
        frame = self._frame(np.arange(20, dtype=float) * 0.2)
        candidate, segments = auto_v0.select_direct_candidate(
            frame,
            station="ATU1",
            fit_end_date=frame["date"].iloc[-1],
            profile=auto_v0.load_auto_v0_profile(),
        )
        self.assertEqual(candidate["failure_reason"], "insufficient_fit_rows")
        self.assertTrue(segments.empty)

    def test_segment_wrapper_centers_large_displacement_without_changing_slope(self):
        values = 1e6 + np.arange(100, dtype=float) * 0.2
        result = auto_v0.segment_piecewise_linear_signal(
            pd.DataFrame(
                {"date": pd.date_range("2016-01-01", periods=100), "displacement": values}
            )
        )
        self.assertEqual(result["selected_segment_count"], 1)
        np.testing.assert_allclose(result["segments"][0]["slope_mm_per_day"], 0.2, atol=1e-12)

    def test_real_fit_windows_resolve_numerically_before_scientific_gate(self):
        frame = pd.read_csv(
            ROOT / "data" / "ootang_kinematics_long.csv",
            parse_dates=["date"],
        )
        fit_end = pd.Timestamp("2019-02-02")
        expected = {
            "ATU1": (
                "unavailable",
                "first_break_is_not_accelerating",
                [58, 232, 138, 30, 228, 261],
            ),
            "ATU2": (
                "unavailable",
                "first_break_is_not_accelerating",
                [30, 272, 125, 30, 232, 258],
            ),
            "ATU3": (
                "unavailable",
                "first_break_is_not_accelerating",
                [51, 250, 126, 30, 238, 252],
            ),
            "ATU4": (
                "unavailable",
                "first_break_is_not_accelerating",
                [40, 263, 124, 30, 237, 253],
            ),
            "ATU5": (
                "unavailable",
                "first_break_is_not_accelerating",
                [53, 250, 118, 45, 238, 243],
            ),
            "MJ1": (
                "initial_segment_selected",
                None,
                [210, 126, 119, 153, 106, 233],
            ),
            "MJ3": (
                "initial_segment_selected",
                None,
                [200, 103, 155, 165, 191, 133],
            ),
            "MJ9": (
                "unavailable",
                "nonpositive_initial_segment_slope",
                [207, 101, 170, 184, 166, 119],
            ),
        }
        profile = auto_v0.load_auto_v0_profile()
        for station, (
            selection_status,
            failure_reason,
            segment_lengths,
        ) in expected.items():
            with self.subTest(station=station):
                station_frame = frame.loc[frame["station"].eq(station)].copy()
                candidate, segments = auto_v0.select_direct_candidate(
                    station_frame,
                    station=station,
                    fit_end_date=fit_end,
                    profile=profile,
                )
                self.assertEqual(candidate["selection_status"], selection_status)
                self.assertEqual(candidate["failure_reason"], failure_reason)
                self.assertEqual(candidate["selected_segment_count"], 6)
                self.assertEqual(segments["segment_n"].tolist(), segment_lengths)
                self.assertTrue(segments["sse"].ge(0.0).all())


class AutoV0MaterializedTests(unittest.TestCase):
    def test_current_bundle_is_eight_station_fit_only_nonformal(self):
        output_dir = auto_v0.DEFAULT_OUTPUT_DIR
        manifest_path = output_dir / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["case"], "ootang")
        self.assertFalse(manifest["formal_warning_output"])
        self.assertTrue(manifest["candidate_v0_only"])
        self.assertFalse(manifest["vajont_used"])
        self.assertEqual(
            manifest["method"]["segment_statistics_origin"],
            "per_segment_start",
        )
        self.assertEqual(manifest["method"]["sse_roundoff_multiplier"], 32.0)
        for record in manifest["implementation_sources"].values():
            source_path = Path(record["path"])
            if not source_path.is_absolute():
                source_path = ROOT / source_path
            self.assertEqual(
                hashlib.sha256(source_path.read_bytes()).hexdigest(),
                record["sha256"],
            )
        candidates = pd.read_csv(output_dir / "candidates.csv")
        segments = pd.read_csv(output_dir / "segments.csv")
        self.assertEqual(candidates["station"].tolist(), list(OOTANG_STATIONS))
        self.assertEqual(len(candidates), 8)
        self.assertTrue(candidates["candidate_v0_only"].all())
        self.assertFalse(candidates["formal_warning_output"].any())
        self.assertFalse(candidates["vajont_used"].any())
        self.assertNotIn("warning_color", candidates.columns)
        self.assertTrue(set(candidates["selection_status"]) <= {
            "initial_segment_selected", "stable_full_fit_baseline", "unavailable"
        })
        unavailable = candidates["selection_status"].eq("unavailable")
        self.assertTrue(
            candidates.loc[
                unavailable,
                [
                    "candidate_v_mm_per_day",
                    "selected_velocity_mean_mm_per_day",
                    "selected_velocity_sigma_mm_per_day",
                    "candidate_v0_mm_per_day",
                ],
            ].isna().all().all()
        )
        self.assertTrue((pd.to_datetime(candidates["fit_end_date"]) < pd.Timestamp("2019-02-03")).all())
        self.assertTrue((pd.to_datetime(segments["fit_end_date"]) < pd.Timestamp("2019-02-03")).all())
        for record in manifest["outputs"].values():
            path = Path(record["path"])
            if not path.is_absolute():
                path = ROOT / path
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
        svg_lines = (output_dir / "candidate_diagnostics.svg").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertFalse(any(line.rstrip() != line for line in svg_lines))

    def test_output_directory_override_is_rejected(self):
        with self.assertRaises(auto_v0.AutoV0OutputError):
            auto_v0.write_auto_v0_candidates(output_dir=ROOT / "figures" / "convlstm")

    def test_diagnostic_bundle_is_deterministic(self):
        names = [
            "candidates.csv",
            "segments.csv",
            "candidate_diagnostics.png",
            "candidate_diagnostics.svg",
        ]
        with tempfile.TemporaryDirectory() as directory:
            temporary_output = Path(directory) / "bundle"
            auto_v0.write_auto_v0_candidates(output_dir=temporary_output)
            for name in names:
                live_hash = hashlib.sha256(
                    (auto_v0.DEFAULT_OUTPUT_DIR / name).read_bytes()
                ).hexdigest()
                rerun_hash = hashlib.sha256(
                    (temporary_output / name).read_bytes()
                ).hexdigest()
                self.assertEqual(live_hash, rerun_hash, name)

    def test_cli_does_not_expose_output_override(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                auto_v0._parse_args(["--output-dir", "figures/convlstm"])


if __name__ == "__main__":
    unittest.main()
