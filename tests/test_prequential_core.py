"""Parity and fail-closed tests for the pure v1 prequential core."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import math
from pathlib import Path
import sys
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_monitor as e1  # noqa: E402
from monitoring import prequential_core as core  # noqa: E402


def _optional_float(value: object) -> float | None:
    number = float(value)
    return None if math.isnan(number) else number


class PrequentialCoreTests(unittest.TestCase):
    """Lock v1 mathematical parity, immutability, and input rejection."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = e1.load_config(e1.DEFAULT_CONFIG_PATH)

    def test_state_is_immutable_and_codec_hash_matches_e1(self):
        state = core.new_station_state_v1()
        encoded = core.encode_station_state_v1(state)
        digest = core.station_state_sha256_v1(state)
        self.assertEqual(core.decode_station_state_v1(encoded), state)
        self.assertEqual(core.verify_station_state_v1(encoded, digest), state)

        e1_state = e1._StationState(  # noqa: SLF001 - intentional parity lock
            expert_count=6,
            initial_alpha=0.2,
            next_issue_reset_reason="initial_fold_start",
        )
        self.assertEqual(
            digest,
            e1._station_state_sha256(e1_state),  # noqa: SLF001
        )
        with self.assertRaises(FrozenInstanceError):
            state.history_count = 1

        live_state = core.new_station_state_v1(reset_reason="live_epoch_start")
        self.assertEqual(live_state.next_issue_reset_reason, "live_epoch_start")
        self.assertEqual(
            core.decode_station_state_v1(core.encode_station_state_v1(live_state)),
            live_state,
        )

    def test_codec_rejects_tamper_noncanonical_and_bad_hash(self):
        state = core.new_station_state_v1()
        encoded = core.encode_station_state_v1(state)
        mutations = (
            encoded.replace(b'"history_count":0', b'"history_count":1'),
            encoded.replace(b'"expert_count":6', b'"expert_count":5'),
            encoded.replace(b'"alpha":0.2', b'"alpha":NaN'),
            encoded[:-1] + b',"alpha":0.2}',
            b" " + encoded,
        )
        for payload in mutations:
            with self.subTest(payload=payload[:40]):
                with self.assertRaises(core.PrequentialCoreError):
                    core.decode_station_state_v1(payload)
        with self.assertRaises(core.PrequentialCoreError):
            core.verify_station_state_v1(encoded, "0" * 64)
        with self.assertRaises(core.PrequentialCoreError):
            core.verify_station_state_v1(encoded, "BAD")

    def test_issue_is_pure_and_warmup_is_exactly_sixty_reveals(self):
        state = core.new_station_state_v1()
        initial_encoding = core.encode_station_state_v1(state)
        experts = (6.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        for day in range(60):
            issue = core.issue_station(state, experts)
            self.assertEqual(issue.forecast_action, "abstain")
            self.assertEqual(issue.uncertainty_action, "abstain_rewarm")
            self.assertIsNone(issue.conformal_q_mm)
            reveal = core.reveal_station(state, issue, 3.0)
            self.assertIsNone(reveal.anomaly_p_value)
            self.assertEqual(reveal.updated_state.history_count, day + 1)
            state = reveal.next_state
        active = core.issue_station(state, experts)
        self.assertEqual(active.forecast_action, "point_forecast")
        self.assertEqual(active.uncertainty_action, "interval_available")
        self.assertEqual(active.conformal_q_mm, 2.0)
        self.assertEqual(active.interval_lower_mm, -1.0)
        self.assertEqual(active.interval_upper_mm, 3.0)
        self.assertEqual(
            initial_encoding,
            core.encode_station_state_v1(core.new_station_state_v1()),
        )

    def test_history_windows_are_bounded_and_drift_resets_only_next_issue(self):
        state = core.new_station_state_v1()
        for _ in range(220):
            issue = core.issue_station(state, (0.0,) * 6)
            state = core.reveal_station(state, issue, 0.0).next_state
        self.assertEqual(state.history_count, 220)
        self.assertEqual(len(state.absolute_residuals), 180)
        self.assertEqual(len(state.underprediction_residuals), 180)
        self.assertEqual(len(state.drift_values), 180)

        drift_state = core.StationStateV1(
            cumulative_loss=(0.0,) * 6,
            history_count=179,
            absolute_residuals=(0.0,) * 179,
            underprediction_residuals=(0.0,) * 179,
            drift_values=(0.0,) * 90 + (1.0,) * 89,
            alpha=0.2,
            next_issue_reset_reason="none",
        )
        issue = core.issue_station(drift_state, (0.0,) * 6)
        reveal = core.reveal_station(drift_state, issue, 1.0)
        self.assertTrue(reveal.drift_detected)
        self.assertIsNotNone(reveal.drift_cut_index)
        self.assertEqual(reveal.updated_state.history_count, 180)
        self.assertEqual(reveal.updated_state.next_issue_reset_reason, "none")
        self.assertEqual(reveal.next_state.history_count, 0)
        self.assertEqual(
            reveal.next_state.next_issue_reset_reason,
            "drift_detected_previous_date",
        )
        self.assertNotEqual(reveal.updated_state_sha256, reveal.state_after_sha256)
        next_issue = core.issue_station(reveal.next_state, (0.0,) * 6)
        self.assertEqual(
            next_issue.state_reset_reason, "drift_detected_previous_date"
        )

    def test_invalid_numeric_expert_state_and_forged_issue_fail_closed(self):
        state = core.new_station_state_v1()
        for experts in (
            (0.0,) * 5,
            (0.0,) * 7,
            (0.0, 0.0, 0.0, 0.0, 0.0, math.nan),
            (0.0, 0.0, 0.0, 0.0, 0.0, math.inf),
        ):
            with self.subTest(experts=experts):
                with self.assertRaises(core.PrequentialCoreError):
                    core.issue_station(state, experts)
        with self.assertRaises(core.PrequentialCoreError):
            core.StationStateV1(cumulative_loss=(0.0,) * 5)
        with self.assertRaises(core.PrequentialCoreError):
            core.StationStateV1(cumulative_loss=(0.0,) * 5 + (math.nan,))
        issue = core.issue_station(state, (0.0,) * 6)
        with self.assertRaises(core.PrequentialCoreError):
            core.reveal_station(state, issue, math.inf)
        with self.assertRaises(core.PrequentialCoreError):
            core.reveal_station(
                state, replace(issue, point_forecast_mm=1.0), 0.0
            )

    def test_synthetic_multiday_replay_matches_e1_field_by_field(self):
        stations = self.profile["source_contract"]["stations"]
        dates = pd.date_range("2025-01-01", periods=75, freq="D")
        source_records: list[dict[str, object]] = []
        for date_index, date in enumerate(dates):
            sign = 1.0 if date_index % 2 == 0 else -1.0
            for station in stations:
                for seed in range(5):
                    source_records.append(
                        {
                            "seed": seed,
                            "fold": 1,
                            "date": date,
                            "station": station,
                            "actual": sign * 3.0,
                            "persistence": sign * 6.0,
                            "p50": 0.0,
                        }
                    )
        source = pd.DataFrame.from_records(source_records)
        expected_station, expected_site = e1.build_timelines(source, self.profile)

        states = {
            station: core.new_station_state_v1() for station in stations
        }
        actual_rows: list[dict[str, object]] = []
        site_rows: list[core.SiteAggregateV1] = []
        for date_index, date in enumerate(dates):
            sign = 1.0 if date_index % 2 == 0 else -1.0
            issues = {
                station: core.issue_station(
                    states[station], (sign * 6.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                )
                for station in stations
            }
            available: dict[str, float | None] = {}
            for station in stations:
                state = states[station]
                issue = issues[station]
                reveal = core.reveal_station(state, issue, sign * 3.0)
                states[station] = reveal.next_state
                available[station] = reveal.anomaly_score
                actual_rows.append(
                    {
                        "date": date.strftime("%Y-%m-%d"),
                        "station": station,
                        "issue": issue,
                        "reveal": reveal,
                    }
                )
            site_rows.append(core.aggregate_site_scores(available))

        self.assertEqual(len(actual_rows), len(expected_station))
        for expected, actual in zip(
            expected_station.to_dict("records"), actual_rows
        ):
            with self.subTest(date=actual["date"], station=actual["station"]):
                issue = actual["issue"]
                reveal = actual["reveal"]
                self.assertEqual(actual["date"], expected["date"])
                self.assertEqual(actual["station"], expected["station"])
                issue_pairs = {
                    "state_before_sha256": "issue_state_before_sha256",
                    "state_reset_reason": "issue_state_reset_reason",
                    "history_count": "issue_history_count",
                    "forecast_action": "issue_forecast_action",
                    "uncertainty_action": "issue_uncertainty_action",
                    "eta": "issue_eta",
                    "point_forecast_mm": "issue_point_forecast_mm",
                    "fallback_persistence_mm": "issue_fallback_persistence_mm",
                    "aci_alpha": "issue_aci_alpha",
                    "conformal_history_count": "issue_conformal_history_count",
                }
                for core_name, e1_name in issue_pairs.items():
                    self.assertEqual(getattr(issue, core_name), expected[e1_name])
                self.assertEqual(
                    issue.experts_mm,
                    tuple(
                        expected[column]
                        for column in (
                            "issue_expert_persistence_mm",
                            "issue_expert_seed0_p50_mm",
                            "issue_expert_seed1_p50_mm",
                            "issue_expert_seed2_p50_mm",
                            "issue_expert_seed3_p50_mm",
                            "issue_expert_seed4_p50_mm",
                        )
                    ),
                )
                self.assertEqual(
                    issue.weights,
                    tuple(
                        expected[column]
                        for column in (
                            "issue_weight_persistence",
                            "issue_weight_seed0",
                            "issue_weight_seed1",
                            "issue_weight_seed2",
                            "issue_weight_seed3",
                            "issue_weight_seed4",
                        )
                    ),
                )
                optional_issue_pairs = {
                    "conformal_q_mm": "issue_conformal_q_mm",
                    "interval_lower_mm": "issue_interval_lower_mm",
                    "interval_upper_mm": "issue_interval_upper_mm",
                }
                for core_name, e1_name in optional_issue_pairs.items():
                    self.assertEqual(
                        getattr(issue, core_name), _optional_float(expected[e1_name])
                    )
                reveal_pairs = {
                    "actual_mm": "reveal_actual_mm",
                    "point_absolute_error_mm": "reveal_point_absolute_error_mm",
                    "persistence_absolute_error_mm": (
                        "reveal_persistence_absolute_error_mm"
                    ),
                    "underprediction_residual_mm": (
                        "reveal_underprediction_residual_mm"
                    ),
                    "absolute_residual_surprise_rank": (
                        "reveal_absolute_residual_surprise_rank"
                    ),
                    "drift_detected": "reveal_drift_detected",
                    "aci_alpha_after_update": "reveal_aci_alpha_after_update",
                    "history_count_after_update": (
                        "reveal_history_count_after_update"
                    ),
                    "state_reset_after_update": (
                        "reveal_state_reset_after_update"
                    ),
                    "updated_state_sha256": "reveal_updated_state_sha256",
                    "state_after_sha256": "reveal_state_after_sha256",
                }
                for core_name, e1_name in reveal_pairs.items():
                    self.assertEqual(getattr(reveal, core_name), expected[e1_name])
                optional_reveal_pairs = {
                    "interval_width_mm": "reveal_interval_width_mm",
                    "anomaly_p_value": "reveal_anomaly_p_value",
                    "anomaly_score": "reveal_anomaly_score",
                    "drift_cut_index": "reveal_drift_cut_index",
                }
                for core_name, e1_name in optional_reveal_pairs.items():
                    self.assertEqual(
                        getattr(reveal, core_name), _optional_float(expected[e1_name])
                    )
                covered = expected["reveal_interval_covered"]
                expected_covered = None if pd.isna(covered) else bool(covered)
                self.assertEqual(reveal.interval_covered, expected_covered)

        for expected, actual in zip(expected_site.to_dict("records"), site_rows):
            self.assertEqual(
                actual.available_station_score_count,
                expected["available_station_score_count"],
            )
            self.assertEqual(
                actual.abstained_station_score_count,
                expected["abstained_station_score_count"],
            )
            self.assertEqual(actual.site_score_status, expected["site_score_status"])
            self.assertEqual(
                actual.cross_block_min_of_block_max_anomaly_score,
                _optional_float(
                    expected["cross_block_min_of_block_max_anomaly_score"]
                ),
            )
            for block_name in ("O1", "O2", "O3"):
                block = actual.block(block_name)
                prefix = f"block_{block_name.lower()}"
                self.assertEqual(
                    block.available_station_count,
                    expected[f"{prefix}_available_station_count"],
                )
                self.assertEqual(
                    block.expected_station_count,
                    expected[f"{prefix}_expected_station_count"],
                )
                self.assertEqual(
                    block.contributor_station,
                    expected[f"{prefix}_contributor_station"],
                )
                self.assertEqual(
                    block.maximum_anomaly_score,
                    _optional_float(expected[f"{prefix}_max_anomaly_score"]),
                )

    def test_site_aggregation_status_ties_and_invalid_scores(self):
        incomplete = core.aggregate_site_scores({"MJ9": 2.0, "ATU4": 1.0})
        self.assertEqual(incomplete.site_score_status, "abstain_spatial_incomplete")
        self.assertIsNone(
            incomplete.cross_block_min_of_block_max_anomaly_score
        )
        subset = core.aggregate_site_scores(
            {"MJ9": 2.0, "MJ1": 2.0, "ATU4": 1.0, "ATU2": 3.0}
        )
        self.assertEqual(subset.site_score_status, "available_subset_all_blocks")
        self.assertEqual(subset.block("O1").contributor_station, "MJ9")
        self.assertEqual(
            subset.cross_block_min_of_block_max_anomaly_score, 1.0
        )
        with self.assertRaises(core.PrequentialCoreError):
            core.aggregate_site_scores({"UNKNOWN": 1.0})
        with self.assertRaises(core.PrequentialCoreError):
            core.aggregate_site_scores({"MJ9": math.nan})
        with self.assertRaises(core.PrequentialCoreError):
            core.aggregate_site_scores({"MJ9": math.inf})


if __name__ == "__main__":
    unittest.main()
