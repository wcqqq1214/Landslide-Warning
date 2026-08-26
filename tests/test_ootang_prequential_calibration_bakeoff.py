"""Causality and materialized-output contracts for the calibration bakeoff."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import (  # noqa: E402
    ootang_prequential_calibration_bakeoff as bakeoff,
)


CONFIG = bakeoff.DEFAULT_CONFIG_PATH
OUTPUT_DIR = bakeoff.DEFAULT_OUTPUT_DIR


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PrequentialCalibrationBakeoffTests(unittest.TestCase):
    """Lock the retrospective adapter to issue-before-reveal semantics."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = bakeoff.load_config(CONFIG)
        cls.source = bakeoff.validate_e1_source(cls.profile)
        names = cls.profile["outputs"]
        cls.timeline = bakeoff._read_csv(
            OUTPUT_DIR / names["candidate_timeline"],
            label="candidate_timeline",
        )
        cls.metrics = bakeoff._read_csv(
            OUTPUT_DIR / names["candidate_metrics"],
            label="candidate_metrics",
        )
        cls.pairwise = bakeoff._read_csv(
            OUTPUT_DIR / names["pairwise_comparison"],
            label="pairwise_comparison",
        )
        cls.manifest_path = OUTPUT_DIR / names["manifest"]
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))

    @staticmethod
    def _synthetic_source(
        profile: dict[str, object],
        *,
        first_actual_delta: float = 0.0,
    ) -> bakeoff.ValidatedE1Source:
        stations = profile["source_contract"]["stations"]  # type: ignore[index]
        records: list[dict[str, object]] = []
        dates = ("2030-01-01", "2030-01-02")
        for date_index, date in enumerate(dates):
            for station_index, station in enumerate(stations):
                point = 100.0 + station_index
                actual = point + 1.0 + date_index
                if date_index == 0 and station == stations[0]:
                    actual += first_actual_delta
                records.append(
                    {
                        "fold": 1,
                        "date": date,
                        "station": station,
                        "issue_state_reset_reason": (
                            "initial_fold_start" if date_index == 0 else "none"
                        ),
                        "issue_history_count": date_index,
                        "issue_point_forecast_mm": point,
                        "issue_interval_lower_mm": float("nan"),
                        "issue_interval_upper_mm": float("nan"),
                        "issue_aci_alpha": 0.2,
                        "issue_batch_sha256": str(date_index + 1) * 64,
                        "reveal_actual_mm": actual,
                        "reveal_state_reset_after_update": False,
                    }
                )
        return bakeoff.ValidatedE1Source(
            station_timeline=pd.DataFrame.from_records(records),
            site_timeline=pd.DataFrame(),
            metrics=pd.DataFrame(),
            manifest={},
            artifact_records={},
        )

    def test_config_is_byte_exact_and_rejects_even_whitespace_mutation(self):
        self.assertEqual(
            _sha256(CONFIG), bakeoff.EXPECTED_CONFIG_FILE_SHA256
        )
        self.assertEqual(
            tuple(self.profile["design"]["candidate_order"]),
            bakeoff.METHODS,
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            exact = Path(directory) / "exact.json"
            exact.write_bytes(CONFIG.read_bytes())
            self.assertEqual(bakeoff.load_config(exact), self.profile)

            mutated = Path(directory) / "mutated.json"
            mutated.write_bytes(CONFIG.read_bytes() + b"\n")
            with self.assertRaises(bakeoff.CalibrationBakeoffConfigError):
                bakeoff.load_config(mutated)

    def test_same_date_actual_cannot_change_any_issue_or_issue_batch_hash(self):
        self.assertEqual(
            set(bakeoff.ISSUE_SOURCE_COLUMNS)
            & set(bakeoff.REVEAL_SOURCE_COLUMNS),
            {"station"},
        )
        self.assertFalse(
            any(
                column.startswith("reveal_")
                for column in bakeoff.ISSUE_SOURCE_COLUMNS
            )
        )
        original_source = self._synthetic_source(self.profile)
        mutated_source = self._synthetic_source(
            self.profile,
            first_actual_delta=50.0,
        )
        original = bakeoff.build_candidate_timeline(
            original_source, self.profile
        )
        mutated = bakeoff.build_candidate_timeline(
            mutated_source, self.profile
        )

        first_date = "2030-01-01"
        second_date = "2030-01-02"
        issue_columns = [
            "method",
            "fold",
            "date",
            "station",
            "source_issue_batch_sha256",
            *[
                column
                for column in original.columns
                if column.startswith("issue_")
            ],
            "previous_candidate_issue_batch_sha256",
            "candidate_issue_batch_sha256",
        ]
        pd.testing.assert_frame_equal(
            original.loc[original["date"] == first_date, issue_columns].reset_index(
                drop=True
            ),
            mutated.loc[mutated["date"] == first_date, issue_columns].reset_index(
                drop=True
            ),
            check_exact=True,
        )
        first_original = original.loc[original["date"] == first_date]
        first_mutated = mutated.loc[mutated["date"] == first_date]
        self.assertEqual(len(first_original), 24)
        self.assertEqual(
            first_original["candidate_issue_batch_sha256"].nunique(), 1
        )
        self.assertEqual(
            first_original["candidate_issue_batch_sha256"].iloc[0],
            first_mutated["candidate_issue_batch_sha256"].iloc[0],
        )

        target_station = self.profile["source_contract"]["stations"][0]
        next_original = original.loc[
            (original["date"] == second_date)
            & (original["station"] == target_station)
        ].set_index("method")
        next_mutated = mutated.loc[
            (mutated["date"] == second_date)
            & (mutated["station"] == target_station)
        ].set_index("method")
        self.assertTrue(
            (
                next_original["issue_calibration_state_before_sha256"]
                != next_mutated["issue_calibration_state_before_sha256"]
            ).all()
        )
        self.assertNotEqual(
            original.loc[
                original["date"] == second_date,
                "candidate_issue_batch_sha256",
            ].iloc[0],
            mutated.loc[
                mutated["date"] == second_date,
                "candidate_issue_batch_sha256",
            ].iloc[0],
        )

    def test_real_materialized_outputs_validate_without_replaying_timeline(self):
        bakeoff.validate_outputs(
            self.timeline,
            self.metrics,
            self.pairwise,
            self.source,
            self.profile,
            replay_timeline=False,
        )
        self.assertEqual(
            len(self.timeline),
            len(self.source.station_timeline) * len(bakeoff.METHODS),
        )
        self.assertEqual(len(self.metrics), 108)
        self.assertEqual(len(self.pairwise), 144)

        forbidden_tokens = {
            "winner",
            "rank",
            "ranking",
            "selected",
            "selection",
            "promotion",
        }
        for frame in (self.timeline, self.metrics, self.pairwise):
            for column in frame.columns:
                normalized = str(column).lower()
                self.assertFalse(
                    forbidden_tokens.intersection(normalized.split("_")),
                    column,
                )

        tampered = self.metrics.assign(winner=False)
        with self.assertRaises(bakeoff.CalibrationBakeoffOutputError):
            bakeoff.validate_outputs(
                self.timeline,
                tampered,
                self.pairwise,
                self.source,
                self.profile,
                replay_timeline=False,
            )

    def test_manifest_hashes_match_all_declared_repository_files(self):
        records: list[tuple[str, dict[str, object], str]] = []
        records.append(("profile", self.manifest["profile"], "file_sha256"))
        records.extend(
            (f"implementation.{name}", record, "sha256")
            for name, record in self.manifest["implementation"].items()
        )
        records.extend(
            (f"source.{name}", record, "sha256")
            for name, record in self.manifest["source"]["artifacts"].items()
        )
        records.extend(
            (f"outputs.{name}", record, "sha256")
            for name, record in self.manifest["outputs"].items()
        )
        for label, record, hash_key in records:
            with self.subTest(record=label):
                path = Path(str(record["path"]))
                if not path.is_absolute():
                    path = ROOT / path
                self.assertTrue(path.is_file(), label)
                self.assertEqual(_sha256(path), record[hash_key], label)
                if "size_bytes" in record:
                    self.assertEqual(path.stat().st_size, record["size_bytes"])

        self.assertFalse(self.manifest["claims"]["selection_performed"])
        self.assertFalse(self.manifest["claims"]["promotion_performed"])
        self.assertFalse(
            self.manifest["materialized_validation"]["selection_performed"]
        )
        self.assertFalse(
            self.manifest["materialized_validation"]["promotion_performed"]
        )
        self.assertTrue(
            self.manifest["materialized_validation"][
                "candidate_timeline_deterministically_replayed_from_source"
            ]
        )
        self.assertFalse(
            self.manifest["materialized_validation"][
                "independent_implementation_replay_performed"
            ]
        )
        self.assertFalse(
            self.manifest["materialized_validation"][
                "bundle_wide_sigkill_transaction"
            ]
        )


if __name__ == "__main__":
    unittest.main()
