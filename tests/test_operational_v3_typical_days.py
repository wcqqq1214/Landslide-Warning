"""Contract tests for the Ootang v3 representative-day diagnostic figure."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.operational_v3_full_timeline import (
    write_ootang_v3_full_timeline_figure,
)
from warning.operational_v3_typical_days import (
    REPRESENTATIVE_DAY_RULE_IDS,
    TypicalDayFigureInputError,
    select_representative_days,
    write_ootang_v3_typical_day_figure,
)


class OperationalV3TypicalDayFigureTests(unittest.TestCase):
    def test_selects_earliest_day_for_each_declared_semantic_rule(self):
        rows = pd.DataFrame(
            [
                {
                    "date": "2020-01-07",
                    "site_fusion_status": "valid",
                    "site_confirmed_color": "red",
                    "local_max_candidate_color": "red",
                    "local_attention_status": "none",
                },
                {
                    "date": "2020-01-06",
                    "site_fusion_status": "valid",
                    "site_confirmed_color": "orange",
                    "local_max_candidate_color": "red",
                    "local_attention_status": "none",
                },
                {
                    "date": "2020-01-05",
                    "site_fusion_status": "valid",
                    "site_confirmed_color": "yellow",
                    "local_max_candidate_color": "red",
                    "local_attention_status": "none",
                },
                {
                    "date": "2020-01-04",
                    "site_fusion_status": "candidate_not_site_confirmed",
                    "site_confirmed_color": pd.NA,
                    "local_max_candidate_color": "red",
                    "local_attention_status": "none",
                },
                {
                    "date": "2020-01-03",
                    "site_fusion_status": "candidate_not_site_confirmed",
                    "site_confirmed_color": pd.NA,
                    "local_max_candidate_color": "yellow",
                    "local_attention_status": "none",
                },
                {
                    "date": "2020-01-02",
                    "site_fusion_status": "valid",
                    "site_confirmed_color": "green",
                    "local_max_candidate_color": "blue",
                    "local_attention_status": "localized_blue_attention",
                },
                {
                    "date": "2020-01-01",
                    "site_fusion_status": "valid",
                    "site_confirmed_color": "green",
                    "local_max_candidate_color": "blue",
                    "local_attention_status": "localized_blue_attention",
                },
            ]
        )
        rows["station_count_orange"] = 0
        rows["station_count_red"] = 0
        severe_cluster = rows["date"].eq("2020-01-04")
        rows.loc[severe_cluster, "station_count_orange"] = 2
        rows.loc[severe_cluster, "station_count_red"] = 1

        selected = select_representative_days(rows)

        self.assertEqual(selected["rule_id"].tolist(), list(REPRESENTATIVE_DAY_RULE_IDS))
        self.assertEqual(
            selected["date"].tolist(),
            [
                "2020-01-03",
                "2020-01-01",
                "2020-01-04",
                "2020-01-05",
                "2020-01-06",
                "2020-01-07",
            ],
        )

    def test_rejects_an_empty_representative_day_rule_list(self):
        rows = pd.DataFrame(
            columns=[
                "date",
                "site_fusion_status",
                "site_confirmed_color",
                "local_max_candidate_color",
                "local_attention_status",
                "station_count_orange",
                "station_count_red",
            ]
        )

        with self.assertRaisesRegex(
            TypicalDayFigureInputError,
            "must not be empty",
        ):
            select_representative_days(rows, [])

    def test_writes_traceable_nonformal_editable_figure_bundle(self):
        source_dir = ROOT / "figures" / "warning_operational_draft_v3"
        with tempfile.TemporaryDirectory() as directory:
            original_rc = {
                name: plt.rcParams[name]
                for name in ("font.size", "axes.linewidth", "svg.hashsalt")
            }
            artifacts = write_ootang_v3_typical_day_figure(
                station_timeline_path=(
                    source_dir / "ootang_operational_station_timeline.csv"
                ),
                site_timeline_path=source_dir / "ootang_operational_site_timeline.csv",
                run_manifest_path=source_dir / "ootang_operational_run_manifest.json",
                profile_path=ROOT / "config" / "ootang_operational_run.v3.draft.json",
                output_dir=Path(directory),
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

            repeated = write_ootang_v3_typical_day_figure(
                station_timeline_path=(
                    source_dir / "ootang_operational_station_timeline.csv"
                ),
                site_timeline_path=source_dir / "ootang_operational_site_timeline.csv",
                run_manifest_path=source_dir / "ootang_operational_run_manifest.json",
                profile_path=ROOT / "config" / "ootang_operational_run.v3.draft.json",
                output_dir=Path(directory),
            )

            self.assertEqual(manifest["artifact_status"], "operational_draft_not_formal")
            self.assertFalse(manifest["formal_warning_output"])
            self.assertFalse(manifest["vajont_used"])
            self.assertEqual(
                [row["rule_id"] for row in manifest["representative_days"]],
                list(REPRESENTATIVE_DAY_RULE_IDS),
            )
            self.assertEqual(
                set(manifest["implementation_sources"]),
                {"renderer", "shared_figure_support"},
            )
            for output in manifest["outputs"].values():
                output_path = Path(output["path"])
                self.assertTrue(output_path.is_file())
                self.assertEqual(
                    hashlib.sha256(output_path.read_bytes()).hexdigest(),
                    output["sha256"],
                )
            svg_text = artifacts.svg_path.read_text(encoding="utf-8")
            self.assertIn("<text", svg_text)
            self.assertIn("NON-FORMAL DRAFT", svg_text)
            self.assertNotIn("latest_run", manifest["source_inputs"])
            self.assertNotIn("manifest", manifest["outputs"])
            self.assertTrue(
                all(
                    not Path(record["path"]).is_absolute()
                    for record in manifest["source_inputs"].values()
                )
            )
            self.assertTrue(
                all(
                    not Path(record["path"]).is_absolute()
                    for record in manifest["implementation_sources"].values()
                )
            )
            self.assertEqual(
                original_rc,
                {
                    name: plt.rcParams[name]
                    for name in ("font.size", "axes.linewidth", "svg.hashsalt")
                },
            )
            self.assertEqual(
                first_bundle,
                {
                    path.name: path.read_bytes()
                    for path in (
                        repeated.svg_path,
                        repeated.pdf_path,
                        repeated.png_path,
                        repeated.manifest_path,
                    )
                },
            )

    def test_rejects_a_stale_core_implementation_fingerprint(self):
        source_dir = ROOT / "figures" / "warning_operational_draft_v3"
        manifest = json.loads(
            (source_dir / "ootang_operational_run_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        manifest["implementation_sources"]["runner"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale_manifest = root / "stale_core_manifest.json"
            stale_manifest.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                TypicalDayFigureInputError,
                "fingerprint is stale: runner",
            ):
                write_ootang_v3_typical_day_figure(
                    station_timeline_path=(
                        source_dir / "ootang_operational_station_timeline.csv"
                    ),
                    site_timeline_path=(
                        source_dir / "ootang_operational_site_timeline.csv"
                    ),
                    run_manifest_path=stale_manifest,
                    profile_path=(
                        ROOT / "config" / "ootang_operational_run.v3.draft.json"
                    ),
                    output_dir=root / "output",
                )

    def test_closes_a_figure_when_rendering_fails_after_creation(self):
        source_dir = ROOT / "figures" / "warning_operational_draft_v3"
        initial_figures = set(plt.get_fignums())
        with tempfile.TemporaryDirectory() as directory, patch(
            "warning.operational_v3_typical_days._draw_level_matrix",
            side_effect=RuntimeError("synthetic render failure"),
        ), self.assertRaisesRegex(RuntimeError, "synthetic render failure"):
            write_ootang_v3_typical_day_figure(
                station_timeline_path=(
                    source_dir / "ootang_operational_station_timeline.csv"
                ),
                site_timeline_path=(
                    source_dir / "ootang_operational_site_timeline.csv"
                ),
                run_manifest_path=(
                    source_dir / "ootang_operational_run_manifest.json"
                ),
                profile_path=(
                    ROOT / "config" / "ootang_operational_run.v3.draft.json"
                ),
                output_dir=Path(directory),
            )

        self.assertEqual(set(plt.get_fignums()), initial_figures)

    def test_writes_complete_observed_after_forecast_timeline_bundle(self):
        source_dir = ROOT / "figures" / "warning_operational_draft_v3"
        with tempfile.TemporaryDirectory() as directory:
            artifacts = write_ootang_v3_full_timeline_figure(
                station_timeline_path=(
                    source_dir / "ootang_operational_station_timeline.csv"
                ),
                site_timeline_path=source_dir / "ootang_operational_site_timeline.csv",
                run_manifest_path=source_dir / "ootang_operational_run_manifest.json",
                profile_path=ROOT / "config" / "ootang_operational_run.v3.draft.json",
                output_dir=Path(directory),
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

            repeated = write_ootang_v3_full_timeline_figure(
                station_timeline_path=(
                    source_dir / "ootang_operational_station_timeline.csv"
                ),
                site_timeline_path=source_dir / "ootang_operational_site_timeline.csv",
                run_manifest_path=source_dir / "ootang_operational_run_manifest.json",
                profile_path=ROOT / "config" / "ootang_operational_run.v3.draft.json",
                output_dir=Path(directory),
            )
            output_payloads = {
                name: Path(record["path"]).read_bytes()
                for name, record in manifest["outputs"].items()
            }
            svg_text = artifacts.svg_path.read_text(encoding="utf-8")
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
            "observed_after_forecast_full_timeline_rule_audit",
        )
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertEqual(manifest["timeline_coverage"]["date_count"], 514)
        self.assertEqual(manifest["timeline_coverage"]["station_count"], 8)
        self.assertEqual(manifest["timeline_coverage"]["station_row_count"], 4112)
        self.assertEqual(
            manifest["timeline_coverage"]["site_not_confirmed_count"],
            400,
        )
        self.assertEqual(manifest["timeline_coverage"]["site_missing_count"], 0)
        self.assertEqual(
            manifest["timeline_coverage"]["site_not_confirmed_semantics"],
            "candidate_not_site_confirmed_not_missing",
        )
        self.assertEqual(
            manifest["displayed_fields"],
            {
                "station_axis": "candidate_level",
                "site_axis": "site_confirmed_level",
                "local_axis": "local_max_candidate_level",
            },
        )
        self.assertEqual(
            set(manifest["implementation_sources"]),
            {"renderer", "shared_figure_support"},
        )
        for source in manifest["implementation_sources"].values():
            self.assertFalse(Path(source["path"]).is_absolute())
            source_path = ROOT / source["path"]
            self.assertEqual(
                hashlib.sha256(source_path.read_bytes()).hexdigest(),
                source["sha256"],
            )
        for name, output in manifest["outputs"].items():
            self.assertEqual(
                hashlib.sha256(output_payloads[name]).hexdigest(),
                output["sha256"],
            )
        self.assertIn("complete 514-day", svg_text)
        self.assertIn("Five-level candidate state", svg_text)
        self.assertIn("OPERATIONAL DRAFT", svg_text)
        self.assertIn("OBSERVED-AFTER-FORECAST", svg_text)
        self.assertIn("400/514", svg_text)
        self.assertIn("NOT MISSING", svg_text)
        self.assertNotIn("latest_run", manifest["source_inputs"])
        self.assertTrue(
            all(
                not Path(record["path"]).is_absolute()
                for record in manifest["source_inputs"].values()
            )
        )
        self.assertEqual(first_bundle, repeated_bundle)


if __name__ == "__main__":
    unittest.main()
