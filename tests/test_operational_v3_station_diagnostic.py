"""Contracts for the Ootang v3 all-station combined diagnostic."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.operational_v3_station_diagnostic import (
    StationCombinedDiagnosticInputError,
    write_ootang_v3_station_combined_diagnostic,
)


class OperationalV3StationCombinedDiagnosticTests(unittest.TestCase):
    def test_writes_complete_traceable_nonformal_bundle(self):
        source_dir = ROOT / "figures" / "warning_operational_draft_v3"
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            original_rc = {
                name: plt.rcParams[name]
                for name in ("font.size", "axes.linewidth", "svg.hashsalt")
            }
            artifacts = write_ootang_v3_station_combined_diagnostic(
                station_timeline_path=(
                    source_dir / "ootang_operational_station_timeline.csv"
                ),
                site_timeline_path=source_dir / "ootang_operational_site_timeline.csv",
                run_manifest_path=source_dir / "ootang_operational_run_manifest.json",
                output_dir=output_dir,
            )
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
            first_bundle = {
                path.name: path.read_bytes()
                for path in (
                    artifacts.svg_path,
                    artifacts.pdf_path,
                    artifacts.png_path,
                    artifacts.manifest_path,
                )
            }
            output_payloads = {
                name: Path(record["path"]).read_bytes()
                for name, record in manifest["outputs"].items()
            }
            svg_text = artifacts.svg_path.read_text(encoding="utf-8")

            repeated = write_ootang_v3_station_combined_diagnostic(
                station_timeline_path=(
                    source_dir / "ootang_operational_station_timeline.csv"
                ),
                site_timeline_path=source_dir / "ootang_operational_site_timeline.csv",
                run_manifest_path=source_dir / "ootang_operational_run_manifest.json",
                output_dir=output_dir,
            )
            repeated_bundle = {
                path.name: path.read_bytes()
                for path in (
                    repeated.svg_path,
                    repeated.pdf_path,
                    repeated.png_path,
                    repeated.manifest_path,
                )
            }

        self.assertEqual(manifest["artifact_status"], "operational_draft_not_formal")
        self.assertEqual(
            manifest["artifact_role"],
            "observed_after_forecast_all_station_combined_diagnostic",
        )
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertEqual(
            manifest["observation_timing"]["status"],
            "observed_after_forecast",
        )
        self.assertEqual(
            manifest["timeline_coverage"],
            {
                "complete_station_date_grid": True,
                "date_count": 514,
                "end_date": "2020-06-30",
                "splits": ["calibration", "test"],
                "start_date": "2019-02-03",
                "station_count": 8,
                "station_row_count": 4112,
            },
        )
        self.assertEqual(
            manifest["field_mappings"],
            {
                "cumulative_displacement": "actual",
                "delta_v_trend": "delta_v_state",
                "final_candidate_level": "candidate_level",
                "interval_level": "interval_level",
                "tangent_angle_level": "tangent_angle_level",
                "velocity_level": "velocity_level",
            },
        )
        self.assertEqual(
            manifest["display_layout"]["station_grid"],
            "four_rows_by_two_columns",
        )
        self.assertEqual(
            manifest["display_layout"]["strip_labels"],
            ["I", "V", "ΔV", "T", "F"],
        )
        self.assertEqual(
            set(manifest["implementation_sources"]),
            {"renderer", "shared_figure_support"},
        )
        for source in manifest["implementation_sources"].values():
            self.assertFalse(Path(source["path"]).is_absolute())
            self.assertEqual(
                hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest(),
                source["sha256"],
            )
        for name, record in manifest["outputs"].items():
            self.assertEqual(
                hashlib.sha256(output_payloads[name]).hexdigest(),
                record["sha256"],
            )
        for field in (
            "interval_level",
            "velocity_level",
            "tangent_angle_level",
            "candidate_level",
        ):
            self.assertEqual(sum(manifest["state_counts"][field].values()), 4112)
        self.assertEqual(
            sum(manifest["state_counts"]["delta_v_state"].values()), 4112
        )
        self.assertIn("all-station displacement", svg_text)
        self.assertIn("I=interval", svg_text)
        self.assertIn("OBSERVED-AFTER-FORECAST", svg_text)
        self.assertIn("MJ9 (O1)", svg_text)
        self.assertIn("ATU1 (O3)", svg_text)
        self.assertNotIn("latest_run", manifest["source_inputs"])
        self.assertEqual(
            original_rc,
            {
                name: plt.rcParams[name]
                for name in ("font.size", "axes.linewidth", "svg.hashsalt")
            },
        )
        self.assertEqual(first_bundle, repeated_bundle)

    def test_rejects_an_incomplete_station_date_grid(self):
        source_dir = ROOT / "figures" / "warning_operational_draft_v3"
        station_source = source_dir / "ootang_operational_station_timeline.csv"
        manifest_source = source_dir / "ootang_operational_run_manifest.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incomplete_path = root / "incomplete_station_timeline.csv"
            rows = pd.read_csv(station_source).iloc[:-1]
            rows.to_csv(incomplete_path, index=False)

            manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
            manifest["outputs"]["station_timeline"]["sha256"] = hashlib.sha256(
                incomplete_path.read_bytes()
            ).hexdigest()
            altered_manifest = root / "altered_manifest.json"
            altered_manifest.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                StationCombinedDiagnosticInputError,
                "station_row_count drifted",
            ):
                write_ootang_v3_station_combined_diagnostic(
                    station_timeline_path=incomplete_path,
                    site_timeline_path=(
                        source_dir / "ootang_operational_site_timeline.csv"
                    ),
                    run_manifest_path=altered_manifest,
                    output_dir=root / "output",
                )


if __name__ == "__main__":
    unittest.main()
