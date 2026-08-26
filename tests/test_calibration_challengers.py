"""Causality, formula, determinism, and fail-closed challenger tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import calibration_challengers as cc  # noqa: E402


class CalibrationChallengerTests(unittest.TestCase):
    """Lock the prospective shadow calibrators to their declared protocol."""

    def setUp(self) -> None:
        self.spci_settings = cc.SpciSettings(
            beta_grid=(0.0, 0.1, 0.2),
            n_estimators=10,
            max_depth=2,
            min_samples_leaf=1,
            max_features=1.0,
            bootstrap=True,
            n_jobs=1,
            random_state=17,
        )

    def test_states_are_frozen_canonical_hashed_and_algorithm_separated(self):
        states = (
            cc.new_aci_state(reset_reason="fold_change"),
            cc.new_agaci_state(
                (1.0 / 360.0, 1.0 / 180.0),
                reset_reason="drift_detected_previous_date",
            ),
            cc.new_spci_state(
                self.spci_settings,
                reset_reason="initial_fold_start",
            ),
        )
        hashes = set()
        for state in states:
            with self.subTest(algorithm=state.algorithm):
                encoded = cc.encode_state_canonical_json(state)
                parsed = json.loads(encoded)
                self.assertEqual(parsed["algorithm"], state.algorithm)
                self.assertNotIn(b" ", encoded)
                digest = cc.state_sha256(state)
                self.assertEqual(len(digest), 64)
                self.assertEqual(digest, cc.state_sha256(state))
                hashes.add(digest)
                with self.assertRaises(FrozenInstanceError):
                    state.history_count = 1
        self.assertEqual(len(hashes), 3)

        changed_settings = cc.SpciSettings(
            beta_grid=(0.0, 0.1, 0.2), random_state=18
        )
        self.assertNotEqual(
            cc.state_sha256(cc.new_spci_state(self.spci_settings)),
            cc.state_sha256(cc.new_spci_state(changed_settings)),
        )
        for constructor in (
            lambda: cc.new_aci_state(reset_reason="live_epoch_start"),
            lambda: cc.new_agaci_state((0.01,), reset_reason="bad"),
            lambda: cc.new_spci_state(
                self.spci_settings, reset_reason="manual_reset"
            ),
        ):
            with self.subTest(constructor=constructor):
                with self.assertRaises(cc.CalibrationChallengerError):
                    constructor()

    def test_aci_higher_rank_formula_and_reveal_updates_only_after_issue(self):
        state = cc.AciState(
            history_count=60,
            absolute_residuals=tuple(float(value) for value in range(1, 61)),
            alpha=0.2,
            reset_reason="fold_change",
        )
        before = cc.encode_state_canonical_json(state)
        issue = cc.issue_aci(state, 10.0)
        # ceil((60 + 1) * (1 - .2)) = 49, using one-based higher rank.
        self.assertEqual(issue.conformal_q_mm, 49.0)
        self.assertEqual(issue.interval_lower_mm, -39.0)
        self.assertEqual(issue.interval_upper_mm, 59.0)
        self.assertEqual(issue.state_reset_reason, "fold_change")
        self.assertEqual(before, cc.encode_state_canonical_json(state))

        reveal = cc.reveal_aci(state, issue, 110.0)
        expected_alpha = 0.2 + (1.0 / 180.0) * (0.2 - 1.0)
        self.assertAlmostEqual(reveal.alpha_after_update, expected_alpha)
        self.assertFalse(reveal.interval_covered)
        self.assertEqual(reveal.updated_state.reset_reason, "none")
        self.assertEqual(state.alpha, 0.2)
        # The just-revealed error was unavailable to the prior issue, but is
        # causally visible on the following issue (rank advances from 49 to 50).
        next_issue = cc.issue_aci(reveal.updated_state, 10.0)
        self.assertEqual(next_issue.conformal_q_mm, 50.0)

        covered = cc.reveal_aci(state, issue, 10.0)
        self.assertAlmostEqual(
            covered.alpha_after_update,
            0.2 + (1.0 / 180.0) * 0.2,
        )

    def test_aci_warmup_boundary_and_bounded_window(self):
        state = cc.AciState(
            history_count=59,
            absolute_residuals=tuple(float(value) for value in range(1, 60)),
        )
        issue = cc.issue_aci(state, 0.0)
        self.assertEqual(issue.interval_status, "abstain_rewarm")
        self.assertIsNone(issue.conformal_q_mm)
        state_at_sixty = cc.reveal_aci(state, issue, 100.0).updated_state
        active = cc.issue_aci(state_at_sixty, 0.0)
        self.assertEqual(active.interval_status, "interval_available")
        self.assertEqual(active.conformal_q_mm, 49.0)

        full = cc.AciState(
            history_count=180,
            absolute_residuals=tuple(float(value) for value in range(180)),
        )
        full_issue = cc.issue_aci(full, 0.0)
        updated = cc.reveal_aci(full, full_issue, 999.0).updated_state
        self.assertEqual(updated.history_count, 181)
        self.assertEqual(len(updated.absolute_residuals), 180)
        self.assertEqual(updated.absolute_residuals[0], 1.0)
        self.assertEqual(updated.absolute_residuals[-1], 999.0)

    def test_aci_rejects_nonfinite_and_forged_and_fails_closed_on_bounds(self):
        state = cc.AciState(
            history_count=60,
            absolute_residuals=(1.0,) * 60,
        )
        with self.assertRaises(cc.CalibrationChallengerError):
            cc.issue_aci(state, math.nan)
        with self.assertRaises(cc.CalibrationChallengerError):
            cc.AciState(history_count=1, absolute_residuals=(math.inf,))
        issue = cc.issue_aci(state, 0.0)
        forged = cc.AciIssue(
            state_before_sha256=issue.state_before_sha256,
            state_reset_reason=issue.state_reset_reason,
            history_count=issue.history_count,
            interval_status=issue.interval_status,
            failure_reason=issue.failure_reason,
            point_forecast_mm=issue.point_forecast_mm,
            alpha=issue.alpha,
            conformal_q_mm=issue.conformal_q_mm,
            interval_lower_mm=-999.0,
            interval_upper_mm=999.0,
        )
        with self.assertRaises(cc.CalibrationChallengerError):
            cc.reveal_aci(state, forged, 0.0)
        huge = cc.AciState(
            history_count=60,
            absolute_residuals=(1e308,) * 60,
        )
        self.assertEqual(cc.issue_aci(huge, 1e308).failure_reason, "nonfinite_bounds")
        with mock.patch.object(cc, "_finite_sample_higher", return_value=-1.0):
            crossing = cc.issue_aci(state, 0.0)
        self.assertEqual(crossing.interval_status, "fail_closed")
        self.assertEqual(crossing.failure_reason, "crossing_bounds")

    def test_agaci_first_active_issue_is_uniform_and_update_is_next_day(self):
        # gamma=0 is the predeclared static ACI expert.
        gammas = (0.0, 0.02)
        state = cc.AgaciState(
            gamma_grid=gammas,
            history_count=60,
            absolute_residuals=tuple(float(value) for value in range(1, 61)),
            # First active issue is uniform even if a restored audit state has
            # nonzero historic loss fields.
            lower_cumulative_normalized_pinball_loss=(0.0, 10.0),
            upper_cumulative_normalized_pinball_loss=(5.0, 0.0),
            active_update_count=0,
        )
        issue = cc.issue_agaci(state, 0.0)
        self.assertEqual(issue.aggregation_rule, "ewa_not_boa")
        self.assertEqual(issue.lower_weights, (0.5, 0.5))
        self.assertEqual(issue.upper_weights, (0.5, 0.5))
        self.assertAlmostEqual(issue.eta, math.sqrt(8.0 * math.log(2.0)))
        self.assertAlmostEqual(issue.effective_gamma_lower, 0.01)
        self.assertAlmostEqual(issue.lower_weight_entropy, math.log(2.0))
        self.assertEqual(issue.expert_alphas, (0.2, 0.2))

        reveal = cc.reveal_agaci(state, issue, 100.0)
        self.assertEqual(reveal.lower_normalized_pinball_loss, (1.0, 1.0))
        self.assertEqual(reveal.upper_normalized_pinball_loss, (1.0, 1.0))
        self.assertAlmostEqual(reveal.expert_alphas_after_update[0], 0.2)
        self.assertAlmostEqual(reveal.expert_alphas_after_update[1], 0.184)
        self.assertEqual(reveal.active_update_count_after_update, 1)
        # Today's miss cannot change today's issue, only tomorrow's issue.
        self.assertEqual(issue.expert_alphas, (0.2, 0.2))
        next_issue = cc.issue_agaci(reveal.updated_state, 0.0)
        self.assertEqual(next_issue.expert_alphas, (0.2, 0.184))
        with self.assertRaises(cc.CalibrationChallengerError):
            cc.new_agaci_state((-0.001, 0.0))

    def test_agaci_ewa_formula_daily_side_normalization_and_entropy(self):
        gammas = (0.01, 0.02)
        state = cc.AgaciState(
            gamma_grid=gammas,
            history_count=60,
            absolute_residuals=tuple(float(value) for value in range(1, 61)),
            expert_alphas=(0.1, 0.5),
            lower_cumulative_normalized_pinball_loss=(0.0, 1.0),
            upper_cumulative_normalized_pinball_loss=(1.0, 0.0),
            active_update_count=1,
        )
        issue = cc.issue_agaci(state, 0.0)
        eta = math.sqrt(8.0 * math.log(2.0) / 2.0)
        expected_first = 1.0 / (1.0 + math.exp(-eta))
        self.assertAlmostEqual(issue.lower_weights[0], expected_first)
        self.assertAlmostEqual(issue.upper_weights[1], expected_first)
        self.assertAlmostEqual(sum(issue.lower_weights), 1.0)
        self.assertAlmostEqual(
            issue.effective_gamma_lower,
            sum(w * g for w, g in zip(issue.lower_weights, gammas)),
        )
        self.assertGreaterEqual(issue.lower_weight_entropy, 0.0)

        reveal = cc.reveal_agaci(state, issue, 40.0)
        self.assertEqual(reveal.expert_interval_covered, (True, False))
        lower = reveal.lower_pinball_loss
        upper = reveal.upper_pinball_loss
        self.assertIsNotNone(lower)
        self.assertIsNotNone(upper)
        assert lower is not None and upper is not None
        self.assertAlmostEqual(
            reveal.lower_normalized_pinball_loss[0],  # type: ignore[index]
            1.0,
        )
        self.assertAlmostEqual(
            reveal.lower_normalized_pinball_loss[1],  # type: ignore[index]
            lower[1] / lower[0],
        )
        self.assertAlmostEqual(
            reveal.upper_normalized_pinball_loss[1],  # type: ignore[index]
            1.0,
        )
        self.assertAlmostEqual(
            reveal.upper_normalized_pinball_loss[0],  # type: ignore[index]
            upper[0] / upper[1],
        )
        self.assertAlmostEqual(reveal.expert_alphas_after_update[0], 0.102)
        self.assertAlmostEqual(reveal.expert_alphas_after_update[1], 0.484)

    def test_agaci_fail_closed_crossing_and_nonfinite(self):
        state = cc.AgaciState(
            gamma_grid=(0.01, 0.02),
            history_count=60,
            absolute_residuals=tuple(float(value) for value in range(1, 61)),
            expert_alphas=(0.1, 0.5),
        )
        eta = math.sqrt(8.0 * math.log(2.0))
        with mock.patch.object(
            cc,
            "_ewa_weights",
            return_value=((-2.0, 3.0), eta),
        ):
            crossing = cc.issue_agaci(state, 0.0)
        self.assertEqual(crossing.interval_status, "fail_closed")
        self.assertEqual(crossing.failure_reason, "crossing_bounds")

        huge = cc.AgaciState(
            gamma_grid=(0.01,),
            history_count=60,
            absolute_residuals=(1e308,) * 60,
        )
        failed = cc.issue_agaci(huge, 1e308)
        self.assertEqual(failed.interval_status, "fail_closed")
        self.assertEqual(failed.failure_reason, "nonfinite_bounds")

    def test_spci_exact_pair_warmup_and_current_residual_is_excluded(self):
        state = cc.SpciState(
            settings=self.spci_settings,
            history_count=69,
            signed_residuals=tuple(float(value) for value in range(1, 70)),
        )
        issue = cc.issue_spci(state, 100.0, self.spci_settings)
        self.assertEqual(issue.training_pair_count, 59)
        self.assertEqual(issue.interval_status, "abstain_rewarm")
        reveal = cc.reveal_spci(state, issue, 1000.0)
        self.assertEqual(reveal.signed_residual_mm, 900.0)
        self.assertEqual(issue.training_pair_count, 59)
        next_issue = cc.issue_spci(reveal.updated_state, 100.0)
        self.assertEqual(next_issue.training_pair_count, 60)
        self.assertEqual(next_issue.interval_status, "interval_available")

    def test_spci_lag_features_follow_eq13_latest_to_oldest_order(self):
        state = cc.SpciState(
            settings=self.spci_settings,
            history_count=70,
            signed_residuals=tuple(float(value) for value in range(70)),
        )
        captured = {}

        def capture_distribution(features, targets, query, settings):
            captured["features"] = features.copy()
            captured["targets"] = targets.copy()
            captured["query"] = query.copy()
            weights = cc.np.full(len(targets), 1.0 / len(targets))
            return targets, weights

        with mock.patch.object(
            cc,
            "_qrf_distribution",
            side_effect=capture_distribution,
        ):
            issue = cc.issue_spci(state, 0.0)

        self.assertEqual(issue.interval_status, "interval_available")
        features = captured["features"]
        targets = captured["targets"]
        query = captured["query"]
        self.assertEqual(features[0].tolist(), list(range(9, -1, -1)))
        self.assertEqual(features[-1].tolist(), list(range(68, 58, -1)))
        self.assertEqual(targets.tolist(), list(range(10, 70)))
        self.assertEqual(query.tolist(), list(range(69, 59, -1)))

    def test_spci_uses_signed_asymmetric_residual_quantiles(self):
        residuals = tuple(float(value) for value in range(1, 91))
        state = cc.SpciState(
            settings=self.spci_settings,
            history_count=len(residuals),
            signed_residuals=residuals,
        )
        issue = cc.issue_spci(state, 100.0)
        self.assertEqual(issue.interval_status, "interval_available")
        self.assertGreater(issue.lower_residual_quantile_mm, 0.0)  # type: ignore[arg-type]
        self.assertGreater(issue.interval_lower_mm, 100.0)  # type: ignore[arg-type]
        self.assertGreaterEqual(issue.interval_upper_mm, issue.interval_lower_mm)  # type: ignore[arg-type]
        self.assertIn(issue.selected_beta, self.spci_settings.beta_grid)

        reveal = cc.reveal_spci(state, issue, 93.0)
        self.assertEqual(reveal.signed_residual_mm, -7.0)
        self.assertEqual(reveal.updated_state.signed_residuals[-1], -7.0)
        self.assertNotIn(7.0, reveal.updated_state.signed_residuals[-1:])

    def test_spci_weighted_quantile_excludes_zero_weight_extremes(self):
        values = cc.np.asarray((-999.0, 1.0, 2.0, 999.0))
        weights = cc.np.asarray((0.0, 0.75, 0.25, 0.0))
        self.assertEqual(cc._qrf_weighted_quantile(values, weights, 0.0), 1.0)
        self.assertEqual(cc._qrf_weighted_quantile(values, weights, 0.75), 1.0)
        self.assertEqual(
            cc._qrf_weighted_quantile(values, weights, 0.7500001), 2.0
        )
        self.assertEqual(cc._qrf_weighted_quantile(values, weights, 1.0), 2.0)

        zero_weight_nonfinite = cc.np.asarray((math.nan, 1.0, 2.0, math.inf))
        self.assertEqual(
            cc._qrf_weighted_quantile(
                zero_weight_nonfinite,
                weights,
                0.0,
            ),
            1.0,
        )
        with self.assertRaises(cc.CalibrationChallengerError):
            cc._qrf_weighted_quantile(
                cc.np.asarray((1.0, math.nan)),
                cc.np.asarray((0.5, 0.5)),
                0.5,
            )

    def test_spci_qrf_is_deterministic_settings_hashed_and_window_bounded(self):
        residuals = tuple(float((value % 13) - 6) for value in range(180))
        state = cc.SpciState(
            settings=self.spci_settings,
            history_count=200,
            signed_residuals=residuals,
            reset_reason="drift_detected_previous_date",
        )
        first = cc.issue_spci(state, 3.0)
        second = cc.issue_spci(state, 3.0, random_state=17)
        self.assertEqual(first, second)
        self.assertEqual(first.training_pair_count, 170)
        self.assertEqual(first.state_before_sha256, cc.state_sha256(state))

        reveal = cc.reveal_spci(state, first, -8.0)
        self.assertEqual(len(reveal.updated_state.signed_residuals), 180)
        self.assertEqual(reveal.updated_state.signed_residuals[0], residuals[1])
        self.assertEqual(reveal.updated_state.signed_residuals[-1], -11.0)
        self.assertEqual(reveal.updated_state.reset_reason, "none")
        self.assertNotEqual(
            reveal.updated_state_sha256,
            first.state_before_sha256,
        )
        with self.assertRaises(cc.CalibrationChallengerError):
            cc.issue_spci(state, 3.0, random_state=999)

    def test_spci_fit_nonfinite_and_crossing_fail_closed(self):
        state = cc.SpciState(
            settings=self.spci_settings,
            history_count=70,
            signed_residuals=tuple(float(value) for value in range(70)),
        )
        with mock.patch.object(
            cc.RandomForestRegressor,
            "fit",
            side_effect=ValueError("synthetic fit failure"),
        ):
            failed_fit = cc.issue_spci(state, 0.0)
        self.assertEqual(failed_fit.interval_status, "fail_closed")
        self.assertEqual(failed_fit.failure_reason, "fit_or_qrf_exception")

        with mock.patch.object(
            cc,
            "_qrf_weighted_quantile",
            side_effect=(2.0, 1.0),
        ):
            crossing = cc.issue_spci(state, 0.0)
        self.assertEqual(crossing.interval_status, "fail_closed")
        self.assertEqual(crossing.failure_reason, "crossing_quantiles")

        with mock.patch.object(
            cc,
            "_qrf_weighted_quantile",
            return_value=math.nan,
        ):
            nonfinite = cc.issue_spci(state, 0.0)
        self.assertEqual(nonfinite.interval_status, "fail_closed")
        self.assertEqual(nonfinite.failure_reason, "nonfinite_quantiles")

        with self.assertRaises(cc.CalibrationChallengerError):
            cc.issue_spci(state, math.inf)
        with self.assertRaises(cc.CalibrationChallengerError):
            cc.SpciState(
                settings=self.spci_settings,
                history_count=1,
                signed_residuals=(math.nan,),
            )


if __name__ == "__main__":
    unittest.main()
