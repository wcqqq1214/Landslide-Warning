import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import onset_analysis  # noqa: E402
from warning_events import (  # noqa: E402
    build_onset_targets,
    extract_warning_events,
    score_onset_alerts,
)


class WarningEventTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.date_range("2020-01-01", periods=10)

    def test_extracts_contiguous_events_and_preserves_max_level(self):
        events = extract_warning_events(
            self.dates,
            [0, 1, 2, 0, 0, 1, 1, 1, 0, 0],
        )

        self.assertEqual(len(events), 2)
        self.assertEqual(events["active_days"].tolist(), [2, 3])
        self.assertEqual(events["max_level"].tolist(), [2, 1])
        self.assertEqual(events["duration_days"].tolist(), [2, 3])

    def test_optional_gap_merge_is_explicit(self):
        levels = [0, 1, 1, 0, 1, 0, 0, 0, 0, 0]

        separate = extract_warning_events(self.dates, levels)
        merged = extract_warning_events(
            self.dates,
            levels,
            merge_gap_days=1,
        )

        self.assertEqual(len(separate), 2)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged.loc[0, "active_days"], 3)
        self.assertEqual(merged.loc[0, "duration_days"], 4)

    def test_builds_only_at_risk_future_onset_labels(self):
        targets = build_onset_targets(
            self.dates,
            [0, 0, 1, 1, 0, 0, 0, 1, 0, 0],
            horizons=(1, 3),
        )

        self.assertEqual(targets.loc[1, "onset_h1"], 1)
        self.assertTrue(pd.isna(targets.loc[2, "onset_h1"]))
        self.assertEqual(targets.loc[4, "onset_h3"], 1)
        self.assertTrue(pd.isna(targets.loc[9, "onset_h1"]))

    def test_invalid_initial_window_is_not_called_an_onset(self):
        targets = build_onset_targets(
            self.dates,
            [-1, -1, 1, 1, 0, 0, 0, 0, 0, 0],
            horizons=(1,),
        )

        self.assertFalse(targets.loc[2, "onset_event"])

    def test_event_metrics_separate_hits_and_false_alerts(self):
        metrics, details = score_onset_alerts(
            self.dates,
            [False, False, False, False, True, False, False, False, True, False],
            [False, True, False, False, False, True, False, False, False, True],
            horizon=3,
        )

        self.assertEqual(metrics["event_support"], 2)
        self.assertEqual(metrics["event_hits"], 2)
        self.assertEqual(metrics["false_alert_days"], 1)
        self.assertEqual(metrics["false_alert_events"], 1)
        self.assertEqual(details["lead_days"].tolist(), [3, 3])

    def test_outputs_are_grouped_under_warning_onset_figures(self):
        expected = ROOT / "figures" / "warning_onset"

        self.assertEqual(onset_analysis.OUT_EVENTS_CSV.parent, expected)
        self.assertEqual(onset_analysis.OUT_TARGETS_CSV.parent, expected)
        self.assertEqual(onset_analysis.OUT_INVENTORY_CSV.parent, expected)

    def test_inventory_separates_total_and_forecastable_events(self):
        levels = [-1, 1, 0, 0, 0, 1, 1, 0, 0, 0]
        targets = build_onset_targets(self.dates, levels, horizons=(1, 3))
        events = extract_warning_events(self.dates, levels)
        events = onset_analysis.annotate_event_forecastability(
            events,
            targets,
            horizons=(1, 3),
        )
        inventory = onset_analysis.build_inventory(
            targets,
            events,
            horizons=(1, 3),
        )

        self.assertEqual(inventory["warning_events"].tolist(), [2, 2])
        self.assertEqual(inventory["forecastable_events"].tolist(), [1, 1])


if __name__ == "__main__":
    unittest.main()
