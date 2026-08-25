"""Contracts for the causal machine-only Ootang prequential replay."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_monitor as monitor  # noqa: E402


PREDICTIONS = monitor.DEFAULT_PREDICTIONS_PATH
SOURCE_MANIFEST = monitor.DEFAULT_SOURCE_MANIFEST_PATH
CONFIG = monitor.DEFAULT_CONFIG_PATH


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PrequentialMonitorTests(unittest.TestCase):
    """Exercise the real bundle plus adversarial input mutations."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = monitor.load_config(CONFIG)
        cls.source = monitor.validate_source(
            PREDICTIONS, SOURCE_MANIFEST, cls.profile
        )
        cls.station, cls.site = monitor.build_timelines(
            cls.source.frame, cls.profile
        )
        cls.metrics = monitor.build_metrics(cls.station, cls.profile)
        monitor.validate_outputs(cls.station, cls.site, cls.metrics, cls.profile)
        cls.output_temp = tempfile.TemporaryDirectory(dir=ROOT)
        cls.output_dir = Path(cls.output_temp.name) / "bundle"
        cls.output_manifest = monitor.write_prequential_monitor(
            output_dir=cls.output_dir
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.output_temp.cleanup()

    def _source_manifest_payload(self) -> dict[str, object]:
        return json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))

    def _config_payload(self) -> dict[str, object]:
        return json.loads(CONFIG.read_text(encoding="utf-8"))

    def _write_source_copy(
        self,
        directory: Path,
        frame: pd.DataFrame,
        *,
        update_hash: bool = True,
        update_rows: bool = False,
    ) -> tuple[Path, Path]:
        predictions = directory / PREDICTIONS.name
        writable = frame.copy()
        writable["date"] = pd.to_datetime(writable["date"]).dt.strftime("%Y-%m-%d")
        writable.to_csv(
            predictions,
            index=False,
            lineterminator="\n",
            float_format="%.17g",
        )
        payload = self._source_manifest_payload()
        record = next(
            output
            for output in payload["outputs"]
            if Path(output["path"]).name == PREDICTIONS.name
        )
        if update_hash:
            record["sha256"] = _sha256(predictions)
            record["size_bytes"] = predictions.stat().st_size
        if update_rows:
            record["rows"] = len(frame)
        manifest = directory / "manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        return predictions, manifest

    def _assert_source_rejected(
        self, frame: pd.DataFrame, *, update_rows: bool = False
    ) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            predictions, manifest = self._write_source_copy(
                Path(directory), frame, update_rows=update_rows
            )
            with self.assertRaises(monitor.PrequentialInputError):
                monitor.validate_source(predictions, manifest, self.profile)

    def _assert_output_tamper_rejected(
        self,
        station: pd.DataFrame | None = None,
        site: pd.DataFrame | None = None,
        metrics: pd.DataFrame | None = None,
    ) -> None:
        with self.assertRaises(monitor.PrequentialOutputError):
            monitor.validate_outputs(
                self.station if station is None else station,
                self.site if site is None else site,
                self.metrics if metrics is None else metrics,
                self.profile,
            )

    def test_real_source_and_complete_outputs_have_fixed_rows(self):
        self.assertEqual(len(self.source.frame), 34440)
        self.assertEqual(len(self.station), 6888)
        self.assertEqual(len(self.site), 861)
        self.assertEqual(len(self.metrics), 27)
        self.assertIn("issue_uncertainty_action", self.station.columns)
        self.assertNotIn("issue_probability_action", self.station.columns)
        self.assertFalse(
            self.station.duplicated(["fold", "date", "station"]).any()
        )
        self.assertTrue(
            (self.station.groupby(["fold", "station"]).size() == 287).all()
        )
        warm = self.station["issue_history_count"] < 60
        self.assertTrue(self.station.loc[warm, "reveal_anomaly_score"].isna().all())
        self.assertTrue(
            self.site.loc[
                self.site["available_station_score_count"] == 0,
                "cross_block_min_of_block_max_anomaly_score",
            ].isna().all()
        )
        self.assertTrue(
            (
                self.site.loc[
                    self.site["site_score_status"] == "complete_station_coverage",
                    "available_station_score_count",
                ]
                == 8
            ).all()
        )
        ordered = self.station.sort_values(["fold", "station", "date"])
        previous_effective_state = ordered.groupby(["fold", "station"])[
            "reveal_state_after_sha256"
        ].shift()
        continuing = previous_effective_state.notna()
        self.assertTrue(
            (
                ordered.loc[continuing, "issue_state_before_sha256"]
                == previous_effective_state.loc[continuing]
            ).all()
        )

    def test_materialized_bundle_round_trips_and_recomputes_complete_chain(self):
        station, site, metrics = monitor.read_materialized_bundle(
            self.output_dir, self.profile
        )
        monitor.validate_outputs(station, site, metrics, self.profile)
        self.assertEqual(
            station.iloc[-1]["issue_batch_sha256"],
            site.iloc[-1]["issue_batch_sha256"],
        )
        manifest = json.loads(self.output_manifest.read_text(encoding="utf-8"))
        self.assertTrue(
            manifest["materialized_validation"][
                "issue_chain_recomputed_from_reloaded_csv"
            ]
        )
        self.assertEqual(
            manifest["materialized_validation"]["csv_float_format"], "%.17g"
        )

        names = self.profile["outputs"]
        paths = [
            self.output_dir / names[key]
            for key in ("station_timeline", "site_timeline", "metrics", "manifest")
        ]
        before = {path.name: _sha256(path) for path in paths}
        monitor.write_prequential_monitor(output_dir=self.output_dir)
        after = {path.name: _sha256(path) for path in paths}
        self.assertEqual(before, after)

    def test_config_rejects_missing_unknown_and_semantic_mutation(self):
        mutations = []
        missing = self._config_payload()
        del missing["algorithm"]["same_date_policy"]
        mutations.append(missing)
        unknown = self._config_payload()
        unknown["algorithm"]["unexpected_policy"] = "ignored_if_not_exact"
        mutations.append(unknown)
        semantic_paths = (
            ("source_contract", "same_seed_value_fields"),
            ("source_contract", "quantile_orders"),
            ("algorithm", "expert_loss"),
            ("algorithm", "expert_weight_rule"),
            ("algorithm", "eta_rule"),
            ("algorithm", "conformal_quantile"),
            ("algorithm", "anomaly", "score"),
            ("algorithm", "drift", "input"),
            ("algorithm", "fold_change_policy"),
            ("algorithm", "same_date_policy"),
            ("algorithm", "rewarm_policy", "interval"),
            ("algorithm", "site_score"),
            ("replay_contract", "outcome_use"),
        )
        for path in semantic_paths:
            semantic = self._config_payload()
            container = semantic
            for key in path[:-1]:
                container = container[key]
            container[path[-1]] = "semantic_tamper"
            mutations.append(semantic)

        for index, payload in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory(
                dir=ROOT
            ) as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(monitor.PrequentialConfigError):
                    monitor.load_config(path)

    def test_missing_seed_and_missing_station_fail_closed(self):
        missing_seed = self.source.frame.loc[self.source.frame["seed"] != 4]
        self._assert_source_rejected(missing_seed)

        missing_station = self.source.frame.loc[
            self.source.frame["station"] != "ATU1"
        ]
        self._assert_source_rejected(missing_station)

    def test_duplicate_key_and_broken_date_sequence_fail_closed(self):
        duplicate = self.source.frame.copy()
        duplicate.iloc[-1] = duplicate.iloc[0]
        self._assert_source_rejected(duplicate)

        broken = self.source.frame.copy()
        first_date = broken["date"].min()
        broken.loc[broken["date"] == first_date, "date"] = first_date - pd.Timedelta(
            days=1
        )
        self._assert_source_rejected(broken)

    def test_quantile_crossing_and_nonfinite_value_fail_closed(self):
        crossing = self.source.frame.copy()
        crossing.loc[0, "raw_p10"] = crossing.loc[0, "p50"] + 1.0
        self._assert_source_rejected(crossing)

        nonfinite = self.source.frame.copy()
        nonfinite.loc[0, "p50"] = float("inf")
        self._assert_source_rejected(nonfinite)

    def test_historical_blob_binds_actual_and_previous_natural_day(self):
        self.assertEqual(
            self.source.persistence_reference_commit,
            "1e06629119e08b33ded2540a435e726c2d2da97a",
        )
        self.assertEqual(self.source.persistence_reference_actual_rows, 6888)
        self.assertEqual(self.source.persistence_reference_lag1_rows, 6888)
        self.assertEqual(
            self.source.persistence_reference_first_prior_date, "2018-02-20"
        )

        for column in ("actual", "persistence"):
            with self.subTest(column=column):
                changed = self.source.frame.copy()
                target = (changed["date"] == changed["date"].min()) & (
                    changed["station"] == "ATU1"
                )
                changed.loc[target, column] += 1.0
                self._assert_source_rejected(changed)

    def test_historical_blob_manifest_hash_and_commit_tamper_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            predictions = base / PREDICTIONS.name
            shutil.copy2(PREDICTIONS, predictions)
            for field in ("sha256", "size_bytes", "commit"):
                with self.subTest(field=field):
                    payload = self._source_manifest_payload()
                    if field in {"sha256", "size_bytes"}:
                        record = next(
                            item
                            for item in payload["inputs"]
                            if item["path"] == "data/features.csv"
                        )
                        record[field] = (
                            "0" * 64 if field == "sha256" else 0
                        )
                    else:
                        payload["git"]["commit"] = "0" * 40
                    manifest = base / f"manifest-{field}.json"
                    manifest.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(monitor.PrequentialInputError):
                        monitor.validate_source(predictions, manifest, self.profile)

    def test_source_manifest_hash_drift_fails_before_live_replacement(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            base = Path(directory)
            predictions = base / PREDICTIONS.name
            shutil.copy2(PREDICTIONS, predictions)
            payload = self._source_manifest_payload()
            record = next(
                output
                for output in payload["outputs"]
                if Path(output["path"]).name == PREDICTIONS.name
            )
            record["sha256"] = "0" * 64
            manifest = base / "manifest.json"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            output_dir = base / "live"
            output_dir.mkdir()
            sentinel = output_dir / "station_timeline.csv"
            sentinel.write_text("preserve-me\n", encoding="utf-8")

            with self.assertRaises(monitor.PrequentialInputError):
                monitor.write_prequential_monitor(
                    predictions_path=predictions,
                    source_manifest_path=manifest,
                    output_dir=output_dir,
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve-me\n")
            self.assertEqual([path.name for path in output_dir.iterdir()], [sentinel.name])

    def test_same_date_actual_mutation_cannot_change_issue_or_batch_hash(self):
        changed = self.source.frame.copy()
        target_date = pd.Timestamp("2018-05-01")
        target = (changed["date"] == target_date) & (changed["station"] == "MJ1")
        changed.loc[target, "actual"] += 100.0
        mutated_station, _ = monitor.build_timelines(changed, self.profile)

        original_day = self.station.loc[self.station["date"] == "2018-05-01"]
        mutated_day = mutated_station.loc[mutated_station["date"] == "2018-05-01"]
        issue_and_hash = [*monitor.ISSUE_COLUMNS, "previous_issue_batch_sha256", "issue_batch_sha256"]
        pd.testing.assert_frame_equal(
            original_day.loc[:, issue_and_hash].reset_index(drop=True),
            mutated_day.loc[:, issue_and_hash].reset_index(drop=True),
            check_exact=True,
        )
        self.assertNotEqual(
            original_day.loc[original_day["station"] == "MJ1", "reveal_actual_mm"].item(),
            mutated_day.loc[mutated_day["station"] == "MJ1", "reveal_actual_mm"].item(),
        )
        next_original_hash = self.station.loc[
            self.station["date"] == "2018-05-02", "issue_batch_sha256"
        ].iloc[0]
        next_mutated_hash = mutated_station.loc[
            mutated_station["date"] == "2018-05-02", "issue_batch_sha256"
        ].iloc[0]
        self.assertNotEqual(next_original_hash, next_mutated_hash)

    def test_future_actual_mutation_preserves_complete_causal_prefix(self):
        changed = self.source.frame.copy()
        cutoff = pd.Timestamp("2019-03-01")
        changed.loc[changed["date"] > cutoff, "actual"] += 25.0
        mutated_station, mutated_site = monitor.build_timelines(changed, self.profile)
        original_prefix = self.station.loc[self.station["date"] <= "2019-03-01"]
        mutated_prefix = mutated_station.loc[mutated_station["date"] <= "2019-03-01"]
        pd.testing.assert_frame_equal(
            original_prefix.reset_index(drop=True),
            mutated_prefix.reset_index(drop=True),
            check_exact=True,
        )
        pd.testing.assert_frame_equal(
            self.site.loc[self.site["date"] <= "2019-03-01"].reset_index(drop=True),
            mutated_site.loc[mutated_site["date"] <= "2019-03-01"].reset_index(drop=True),
            check_exact=True,
        )

    def test_input_row_order_does_not_change_timelines_or_hash_chain(self):
        shuffled = self.source.frame.sample(frac=1.0, random_state=20260826)
        station, site = monitor.build_timelines(shuffled, self.profile)
        pd.testing.assert_frame_equal(self.station, station, check_exact=True)
        pd.testing.assert_frame_equal(self.site, site, check_exact=True)

    def test_recomputed_issue_batch_hash_rejects_well_formed_tamper(self):
        station = self.station.copy()
        site = self.site.copy()
        first = station.iloc[0]
        station_day = (station["fold"] == first["fold"]) & (
            station["date"] == first["date"]
        )
        site_day = (site["fold"] == first["fold"]) & (
            site["date"] == first["date"]
        )
        forged = "f" * 64
        station.loc[station_day, "issue_batch_sha256"] = forged
        site.loc[site_day, "issue_batch_sha256"] = forged

        self._assert_output_tamper_rejected(station, site)

    def test_issue_hash_chain_rejects_initial_and_previous_link_tamper(self):
        first_date = self.station.iloc[0]["date"]
        second_date = self.station.loc[
            self.station["date"] != first_date, "date"
        ].iloc[0]
        for date in (first_date, second_date):
            with self.subTest(date=date):
                station = self.station.copy()
                station.loc[
                    station["date"] == date, "previous_issue_batch_sha256"
                ] = "e" * 64
                self._assert_output_tamper_rejected(station=station)

    def test_site_issue_hash_must_match_station_batch(self):
        site = self.site.copy()
        site.loc[site.index[0], "issue_batch_sha256"] = "d" * 64

        self._assert_output_tamper_rejected(site=site)

    def test_station_state_hash_must_link_to_next_issue_within_fold(self):
        station = self.station.copy()
        first_station = self.profile["source_contract"]["stations"][0]
        station_rows = station.loc[
            (station["fold"] == 1) & (station["station"] == first_station)
        ].sort_values("date")
        second_index = station_rows.index[1]
        station.loc[second_index, "issue_state_before_sha256"] = "c" * 64

        self._assert_output_tamper_rejected(station=station)

    def test_reveal_math_and_updated_state_tamper_fail_replay(self):
        active_index = self.station["reveal_anomaly_score"].first_valid_index()
        mutations = {
            "actual": ("reveal_actual_mm", 1.0),
            "absolute_error": ("reveal_point_absolute_error_mm", 1.0),
            "anomaly": ("reveal_anomaly_p_value", 0.01),
        }
        for name, (column, delta) in mutations.items():
            with self.subTest(name=name):
                station = self.station.copy()
                station.loc[active_index, column] += delta
                self._assert_output_tamper_rejected(station=station)

        station = self.station.copy()
        station.loc[active_index, "reveal_updated_state_sha256"] = "b" * 64
        self._assert_output_tamper_rejected(station=station)

    def test_site_aggregation_and_metrics_tamper_fail_replay(self):
        site = self.site.copy()
        active_index = site[
            "cross_block_min_of_block_max_anomaly_score"
        ].first_valid_index()
        site.loc[active_index, "block_o1_max_anomaly_score"] += 1.0
        self._assert_output_tamper_rejected(site=site)

        metrics = self.metrics.copy()
        metrics.loc[metrics.index[0], "point_rmse_mm"] += 1.0
        self._assert_output_tamper_rejected(metrics=metrics)

    def test_bundle_flags_hash_contract_and_no_prohibited_semantics(self):
        manifest = json.loads(self.output_manifest.read_text(encoding="utf-8"))
        expected = {
            "artifact_status": monitor.ARTIFACT_STATUS,
            "formal_warning_output": False,
            "independent_label_used": False,
            "confirmatory_external_validation": False,
            "vajont_used": False,
        }
        for field, value in expected.items():
            self.assertEqual(manifest[field], value)
        self.assertTrue(manifest["issue_hash_chain"]["same_date_actual_excluded"])
        self.assertEqual(
            manifest["issue_hash_chain"]["mode"],
            "retrospective_replay_not_realtime_sealing",
        )
        self.assertTrue(
            manifest["outcome_reveal"]["actual_used_after_reveal_for_update"]
        )
        self.assertFalse(manifest["outcome_reveal"]["actual_used_in_same_date_issue"])
        reference = manifest["source"]["persistence_reference"]
        self.assertEqual(reference["target_actual_rows_validated"], 6888)
        self.assertEqual(
            reference["previous_natural_day_persistence_rows_validated"], 6888
        )
        self.assertEqual(
            reference["sha256"],
            "366188ba1f55fd56b6606f5333e34566fca26c4771dd9745eebfabfcbd4286d1",
        )
        runner = manifest["implementation"]["runner"]
        self.assertEqual(runner["path"], "code/monitoring/ootang_prequential_monitor.py")
        self.assertEqual(runner["sha256"], _sha256(Path(monitor.__file__)))
        self.assertEqual(
            manifest["implementation"]["dependency_lock"]["sha256"],
            _sha256(ROOT / "uv.lock"),
        )
        serialized = json.dumps(manifest).lower()
        for prohibited in ("warning_color", "field_truth", "event_recall", '"far"'):
            self.assertNotIn(prohibited, serialized)
        for frame in (self.station, self.site, self.metrics):
            lowered = {column.lower() for column in frame.columns}
            self.assertTrue(lowered.isdisjoint(monitor.FORBIDDEN_OUTPUT_FIELDS))


if __name__ == "__main__":
    unittest.main()
