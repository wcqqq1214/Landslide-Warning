"""Pure shadow interval calibrators for prospective Ootang evaluation.

The three implementations in this module are deliberately station-local and
have no filesystem or clock dependency.  An issue uses only the immutable
state supplied by the caller; the corresponding reveal returns an immutable
``updated_state`` but never performs a drift reset.  The runner owns ordering,
durable persistence, and construction of a fresh state after an E1 drift.

``agaci_ewa_variant_v1`` is an explicitly named exponential-weights variant.
It is *not* the Bernstein Online Aggregation (BOA) algorithm from AgACI and no
BOA regret guarantee is claimed for it.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from numbers import Integral, Real
from typing import Any, ClassVar, Sequence

import numpy as np
from sklearn.ensemble import RandomForestRegressor


ACI_ALGORITHM_V1 = "aci_v1_control"
AGACI_ALGORITHM_V1 = "agaci_ewa_variant_v1"
SPCI_ALGORITHM_V1 = "spci_qrf_v1"
STATE_CODEC_VERSION_V1 = "ootang-calibration-challenger-state-v1"

MINIMUM_HISTORY_V1 = 60
MAXIMUM_HISTORY_V1 = 180
TARGET_COVERAGE_V1 = 0.8
TARGET_ALPHA_V1 = 0.2
INITIAL_ALPHA_V1 = 0.2
ACI_GAMMA_V1 = 1.0 / 180.0
MINIMUM_ALPHA_V1 = 0.01
MAXIMUM_ALPHA_V1 = 0.5

SPCI_LAG_V1 = 10
SPCI_MINIMUM_PAIRS_V1 = 60
SPCI_LOWER_TAU_V1 = 0.1
SPCI_UPPER_TAU_V1 = 0.9

RESET_REASONS_V1 = frozenset(
    {
        "initial_fold_start",
        "fold_change",
        "drift_detected_previous_date",
        "none",
    }
)


class CalibrationChallengerError(ValueError):
    """Raised for an invalid state, setting, issue, reveal, or numeric input."""


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise CalibrationChallengerError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise CalibrationChallengerError(
            f"{name} must be a finite real number"
        ) from exc
    if not math.isfinite(result):
        raise CalibrationChallengerError(f"{name} must be finite")
    return result


def _positive_float(value: object, name: str) -> float:
    result = _finite_float(value, name)
    if result <= 0.0:
        raise CalibrationChallengerError(f"{name} must be positive")
    return result


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise CalibrationChallengerError(f"{name} must be an integer")
    return int(value)


def _nonnegative_integer(value: object, name: str) -> int:
    result = _integer(value, name)
    if result < 0:
        raise CalibrationChallengerError(f"{name} must be nonnegative")
    return result


def _reset_reason(value: object) -> str:
    if not isinstance(value, str) or value not in RESET_REASONS_V1:
        raise CalibrationChallengerError("reset_reason is not supported")
    return value


def _finite_tuple(
    values: object,
    name: str,
    *,
    nonnegative: bool = False,
) -> tuple[float, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(
        values, Sequence
    ):
        raise CalibrationChallengerError(f"{name} must be a numeric sequence")
    result: list[float] = []
    for index, value in enumerate(values):
        number = _finite_float(value, f"{name}[{index}]")
        if nonnegative and number < 0.0:
            raise CalibrationChallengerError(
                f"{name}[{index}] must be nonnegative"
            )
        result.append(number)
    return tuple(result)


def _validate_history(
    history_count: object,
    residuals: object,
    *,
    signed: bool,
) -> tuple[int, tuple[float, ...]]:
    count = _nonnegative_integer(history_count, "history_count")
    values = _finite_tuple(
        residuals,
        "signed_residuals" if signed else "absolute_residuals",
        nonnegative=not signed,
    )
    expected_length = min(count, MAXIMUM_HISTORY_V1)
    if len(values) != expected_length:
        raise CalibrationChallengerError(
            "residual window length must equal min(history_count, 180)"
        )
    return count, values


def _bounded_alpha(value: object, name: str = "alpha") -> float:
    alpha = _finite_float(value, name)
    if not MINIMUM_ALPHA_V1 <= alpha <= MAXIMUM_ALPHA_V1:
        raise CalibrationChallengerError(
            f"{name} must be in [{MINIMUM_ALPHA_V1}, {MAXIMUM_ALPHA_V1}]"
        )
    return alpha


def _clip_alpha(value: float) -> float:
    return min(max(value, MINIMUM_ALPHA_V1), MAXIMUM_ALPHA_V1)


def _finite_sample_higher(values: tuple[float, ...], alpha: float) -> float:
    """Finite-sample conformal quantile using the requested higher rank."""

    if not values:
        raise CalibrationChallengerError("a conformal quantile needs residuals")
    ordered = sorted(values)
    rank = math.ceil((len(ordered) + 1) * (1.0 - alpha))
    rank = min(max(rank, 1), len(ordered))
    return ordered[rank - 1]


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class AciState:
    """Complete immutable state for one station's fixed ACI control."""

    algorithm: ClassVar[str] = ACI_ALGORITHM_V1
    history_count: int = 0
    absolute_residuals: tuple[float, ...] = ()
    alpha: float = INITIAL_ALPHA_V1
    reset_reason: str = "initial_fold_start"

    def __post_init__(self) -> None:
        count, residuals = _validate_history(
            self.history_count, self.absolute_residuals, signed=False
        )
        object.__setattr__(self, "history_count", count)
        object.__setattr__(self, "absolute_residuals", residuals)
        object.__setattr__(self, "alpha", _bounded_alpha(self.alpha))
        object.__setattr__(self, "reset_reason", _reset_reason(self.reset_reason))


@dataclass(frozen=True, slots=True)
class AciIssue:
    """Issue-time ACI result derived solely from a causal state prefix."""

    algorithm: ClassVar[str] = ACI_ALGORITHM_V1
    state_before_sha256: str
    state_reset_reason: str
    history_count: int
    interval_status: str
    failure_reason: str | None
    point_forecast_mm: float
    alpha: float
    conformal_q_mm: float | None
    interval_lower_mm: float | None
    interval_upper_mm: float | None


@dataclass(frozen=True, slots=True)
class AciReveal:
    """Outcome-time ACI diagnostics and the unreset next state."""

    algorithm: ClassVar[str] = ACI_ALGORITHM_V1
    actual_mm: float
    signed_residual_mm: float
    absolute_residual_mm: float
    interval_covered: bool | None
    alpha_after_update: float
    updated_state_sha256: str
    updated_state: AciState


@dataclass(frozen=True, slots=True)
class AgaciState:
    """State for the EWA endpoint aggregation variant (not BOA AgACI)."""

    algorithm: ClassVar[str] = AGACI_ALGORITHM_V1
    gamma_grid: tuple[float, ...]
    history_count: int = 0
    absolute_residuals: tuple[float, ...] = ()
    expert_alphas: tuple[float, ...] = ()
    lower_cumulative_normalized_pinball_loss: tuple[float, ...] = ()
    upper_cumulative_normalized_pinball_loss: tuple[float, ...] = ()
    active_update_count: int = 0
    reset_reason: str = "initial_fold_start"

    def __post_init__(self) -> None:
        gammas = _finite_tuple(self.gamma_grid, "gamma_grid")
        if not gammas:
            raise CalibrationChallengerError("gamma_grid must not be empty")
        if any(gamma < 0.0 for gamma in gammas):
            raise CalibrationChallengerError(
                "all gamma_grid values must be nonnegative"
            )
        if len(set(gammas)) != len(gammas):
            raise CalibrationChallengerError("gamma_grid values must be unique")
        count, residuals = _validate_history(
            self.history_count, self.absolute_residuals, signed=False
        )
        alphas = (
            (INITIAL_ALPHA_V1,) * len(gammas)
            if not self.expert_alphas
            else tuple(
                _bounded_alpha(value, f"expert_alphas[{index}]")
                for index, value in enumerate(self.expert_alphas)
            )
        )
        lower_loss = (
            (0.0,) * len(gammas)
            if not self.lower_cumulative_normalized_pinball_loss
            else _finite_tuple(
                self.lower_cumulative_normalized_pinball_loss,
                "lower_cumulative_normalized_pinball_loss",
                nonnegative=True,
            )
        )
        upper_loss = (
            (0.0,) * len(gammas)
            if not self.upper_cumulative_normalized_pinball_loss
            else _finite_tuple(
                self.upper_cumulative_normalized_pinball_loss,
                "upper_cumulative_normalized_pinball_loss",
                nonnegative=True,
            )
        )
        expected = len(gammas)
        if not len(alphas) == len(lower_loss) == len(upper_loss) == expected:
            raise CalibrationChallengerError(
                "AgACI alpha and loss vectors must match gamma_grid"
            )
        active_count = _nonnegative_integer(
            self.active_update_count, "active_update_count"
        )
        if active_count > count:
            raise CalibrationChallengerError(
                "active_update_count cannot exceed history_count"
            )
        object.__setattr__(self, "gamma_grid", gammas)
        object.__setattr__(self, "history_count", count)
        object.__setattr__(self, "absolute_residuals", residuals)
        object.__setattr__(self, "expert_alphas", alphas)
        object.__setattr__(
            self,
            "lower_cumulative_normalized_pinball_loss",
            lower_loss,
        )
        object.__setattr__(
            self,
            "upper_cumulative_normalized_pinball_loss",
            upper_loss,
        )
        object.__setattr__(self, "active_update_count", active_count)
        object.__setattr__(self, "reset_reason", _reset_reason(self.reset_reason))


@dataclass(frozen=True, slots=True)
class AgaciIssue:
    """EWA lower/upper aggregation using only previously revealed losses."""

    algorithm: ClassVar[str] = AGACI_ALGORITHM_V1
    aggregation_rule: ClassVar[str] = "ewa_not_boa"
    state_before_sha256: str
    state_reset_reason: str
    history_count: int
    active_update_count: int
    interval_status: str
    failure_reason: str | None
    point_forecast_mm: float
    eta: float
    gamma_grid: tuple[float, ...]
    expert_alphas: tuple[float, ...]
    expert_q_mm: tuple[float, ...] | None
    expert_lower_bounds_mm: tuple[float, ...] | None
    expert_upper_bounds_mm: tuple[float, ...] | None
    lower_weights: tuple[float, ...]
    upper_weights: tuple[float, ...]
    effective_gamma_lower: float
    effective_gamma_upper: float
    lower_weight_entropy: float
    upper_weight_entropy: float
    interval_lower_mm: float | None
    interval_upper_mm: float | None


@dataclass(frozen=True, slots=True)
class AgaciReveal:
    """Outcome losses, alpha updates, and the unreset AgACI variant state."""

    algorithm: ClassVar[str] = AGACI_ALGORITHM_V1
    actual_mm: float
    signed_residual_mm: float
    absolute_residual_mm: float
    interval_covered: bool | None
    expert_interval_covered: tuple[bool, ...] | None
    lower_pinball_loss: tuple[float, ...] | None
    upper_pinball_loss: tuple[float, ...] | None
    lower_normalized_pinball_loss: tuple[float, ...] | None
    upper_normalized_pinball_loss: tuple[float, ...] | None
    expert_alphas_after_update: tuple[float, ...]
    active_update_count_after_update: int
    updated_state_sha256: str
    updated_state: AgaciState


@dataclass(frozen=True, slots=True)
class SpciSettings:
    """Frozen, deterministic QRF settings persisted in every SPCI state."""

    beta_grid: tuple[float, ...] = (0.0, 0.05, 0.1, 0.15, 0.2)
    lag: int = SPCI_LAG_V1
    minimum_pairs: int = SPCI_MINIMUM_PAIRS_V1
    n_estimators: int = 10
    max_depth: int = 2
    min_samples_leaf: int = 1
    max_features: float = 1.0
    bootstrap: bool = True
    n_jobs: int = 1
    random_state: int = 0

    def __post_init__(self) -> None:
        betas = _finite_tuple(self.beta_grid, "beta_grid")
        if not betas:
            raise CalibrationChallengerError("beta_grid must not be empty")
        if any(beta < 0.0 or beta > TARGET_ALPHA_V1 for beta in betas):
            raise CalibrationChallengerError(
                f"beta_grid values must be in [0, {TARGET_ALPHA_V1}]"
            )
        if len(set(betas)) != len(betas):
            raise CalibrationChallengerError("beta_grid values must be unique")
        lag = _integer(self.lag, "lag")
        minimum_pairs = _integer(self.minimum_pairs, "minimum_pairs")
        if lag != SPCI_LAG_V1 or minimum_pairs != SPCI_MINIMUM_PAIRS_V1:
            raise CalibrationChallengerError(
                "spci_qrf_v1 fixes lag=10 and minimum_pairs=60"
            )
        n_estimators = _integer(self.n_estimators, "n_estimators")
        max_depth = _integer(self.max_depth, "max_depth")
        min_samples_leaf = _integer(self.min_samples_leaf, "min_samples_leaf")
        if n_estimators <= 0 or max_depth <= 0 or min_samples_leaf <= 0:
            raise CalibrationChallengerError(
                "forest size, depth, and leaf size must be positive"
            )
        max_features = _positive_float(self.max_features, "max_features")
        if max_features > 1.0:
            raise CalibrationChallengerError("max_features must be at most 1.0")
        if self.bootstrap is not True:
            raise CalibrationChallengerError("bootstrap must be true for QRF weights")
        if _integer(self.n_jobs, "n_jobs") != 1:
            raise CalibrationChallengerError("n_jobs must be 1 for determinism")
        random_state = _integer(self.random_state, "random_state")
        object.__setattr__(self, "beta_grid", betas)
        object.__setattr__(self, "lag", lag)
        object.__setattr__(self, "minimum_pairs", minimum_pairs)
        object.__setattr__(self, "n_estimators", n_estimators)
        object.__setattr__(self, "max_depth", max_depth)
        object.__setattr__(self, "min_samples_leaf", min_samples_leaf)
        object.__setattr__(self, "max_features", max_features)
        object.__setattr__(self, "bootstrap", True)
        object.__setattr__(self, "n_jobs", 1)
        object.__setattr__(self, "random_state", random_state)


@dataclass(frozen=True, slots=True)
class SpciState:
    """Signed-residual state for one deterministic SPCI-QRF challenger."""

    algorithm: ClassVar[str] = SPCI_ALGORITHM_V1
    settings: SpciSettings = SpciSettings()
    history_count: int = 0
    signed_residuals: tuple[float, ...] = ()
    reset_reason: str = "initial_fold_start"

    def __post_init__(self) -> None:
        if not isinstance(self.settings, SpciSettings):
            raise CalibrationChallengerError("settings must be SpciSettings")
        count, residuals = _validate_history(
            self.history_count, self.signed_residuals, signed=True
        )
        object.__setattr__(self, "history_count", count)
        object.__setattr__(self, "signed_residuals", residuals)
        object.__setattr__(self, "reset_reason", _reset_reason(self.reset_reason))


@dataclass(frozen=True, slots=True)
class SpciIssue:
    """Causal SPCI-QRF interval and diagnostics for one target."""

    algorithm: ClassVar[str] = SPCI_ALGORITHM_V1
    state_before_sha256: str
    state_reset_reason: str
    history_count: int
    residual_window_count: int
    training_pair_count: int
    interval_status: str
    failure_reason: str | None
    point_forecast_mm: float
    settings: SpciSettings
    selected_beta: float | None
    lower_residual_quantile_mm: float | None
    upper_residual_quantile_mm: float | None
    interval_lower_mm: float | None
    interval_upper_mm: float | None


@dataclass(frozen=True, slots=True)
class SpciReveal:
    """Outcome-time signed residual and the unreset SPCI state."""

    algorithm: ClassVar[str] = SPCI_ALGORITHM_V1
    actual_mm: float
    signed_residual_mm: float
    absolute_residual_mm: float
    interval_covered: bool | None
    updated_state_sha256: str
    updated_state: SpciState


CalibratorState = AciState | AgaciState | SpciState


def _spci_settings_payload(settings: SpciSettings) -> dict[str, Any]:
    return {
        "beta_grid": list(settings.beta_grid),
        "bootstrap": settings.bootstrap,
        "lag": settings.lag,
        "max_depth": settings.max_depth,
        "max_features": settings.max_features,
        "min_samples_leaf": settings.min_samples_leaf,
        "minimum_pairs": settings.minimum_pairs,
        "n_estimators": settings.n_estimators,
        "n_jobs": settings.n_jobs,
        "random_state": settings.random_state,
    }


def _state_payload(state: CalibratorState) -> dict[str, Any]:
    common = {
        "algorithm": state.algorithm,
        "codec_version": STATE_CODEC_VERSION_V1,
        "history_count": state.history_count,
        "reset_reason": state.reset_reason,
    }
    if isinstance(state, AciState):
        return {
            **common,
            "absolute_residuals": list(state.absolute_residuals),
            "alpha": state.alpha,
        }
    if isinstance(state, AgaciState):
        return {
            **common,
            "absolute_residuals": list(state.absolute_residuals),
            "active_update_count": state.active_update_count,
            "expert_alphas": list(state.expert_alphas),
            "gamma_grid": list(state.gamma_grid),
            "lower_cumulative_normalized_pinball_loss": list(
                state.lower_cumulative_normalized_pinball_loss
            ),
            "upper_cumulative_normalized_pinball_loss": list(
                state.upper_cumulative_normalized_pinball_loss
            ),
        }
    if isinstance(state, SpciState):
        return {
            **common,
            "settings": _spci_settings_payload(state.settings),
            "signed_residuals": list(state.signed_residuals),
        }
    raise CalibrationChallengerError("state is not a calibration challenger state")


def encode_state_canonical_json(state: CalibratorState) -> bytes:
    """Encode a challenger state as deterministic compact canonical JSON."""

    return _canonical_json(_state_payload(state))


def state_sha256(state: CalibratorState) -> str:
    """Hash the full dynamic state and all caller-selectable settings."""

    return hashlib.sha256(encode_state_canonical_json(state)).hexdigest()


def new_aci_state(*, reset_reason: str = "initial_fold_start") -> AciState:
    """Construct a fresh fixed-control ACI state."""

    return AciState(reset_reason=reset_reason)


def issue_aci(state: AciState, point_forecast_mm: Real) -> AciIssue:
    """Issue the fixed ACI control from past absolute residuals only."""

    if not isinstance(state, AciState):
        raise CalibrationChallengerError("state must be AciState")
    point = _finite_float(point_forecast_mm, "point_forecast_mm")
    if state.history_count < MINIMUM_HISTORY_V1:
        return AciIssue(
            state_before_sha256=state_sha256(state),
            state_reset_reason=state.reset_reason,
            history_count=state.history_count,
            interval_status="abstain_rewarm",
            failure_reason=None,
            point_forecast_mm=point,
            alpha=state.alpha,
            conformal_q_mm=None,
            interval_lower_mm=None,
            interval_upper_mm=None,
        )
    qhat = _finite_sample_higher(state.absolute_residuals, state.alpha)
    lower = point - qhat
    upper = point + qhat
    if not math.isfinite(lower) or not math.isfinite(upper):
        return AciIssue(
            state_before_sha256=state_sha256(state),
            state_reset_reason=state.reset_reason,
            history_count=state.history_count,
            interval_status="fail_closed",
            failure_reason="nonfinite_bounds",
            point_forecast_mm=point,
            alpha=state.alpha,
            conformal_q_mm=qhat,
            interval_lower_mm=None,
            interval_upper_mm=None,
        )
    if lower > upper:
        return AciIssue(
            state_before_sha256=state_sha256(state),
            state_reset_reason=state.reset_reason,
            history_count=state.history_count,
            interval_status="fail_closed",
            failure_reason="crossing_bounds",
            point_forecast_mm=point,
            alpha=state.alpha,
            conformal_q_mm=qhat,
            interval_lower_mm=None,
            interval_upper_mm=None,
        )
    return AciIssue(
        state_before_sha256=state_sha256(state),
        state_reset_reason=state.reset_reason,
        history_count=state.history_count,
        interval_status="interval_available",
        failure_reason=None,
        point_forecast_mm=point,
        alpha=state.alpha,
        conformal_q_mm=qhat,
        interval_lower_mm=lower,
        interval_upper_mm=upper,
    )


def reveal_aci(state: AciState, issue: AciIssue, actual_mm: Real) -> AciReveal:
    """Reveal an ACI outcome, then update alpha and append its residual."""

    if not isinstance(state, AciState) or not isinstance(issue, AciIssue):
        raise CalibrationChallengerError("state/issue must be AciState/AciIssue")
    if issue != issue_aci(state, issue.point_forecast_mm):
        raise CalibrationChallengerError("issue does not match the supplied state")
    actual = _finite_float(actual_mm, "actual_mm")
    signed_residual = actual - issue.point_forecast_mm
    absolute_residual = abs(signed_residual)
    if issue.interval_status == "interval_available":
        if issue.interval_lower_mm is None or issue.interval_upper_mm is None:
            raise CalibrationChallengerError("available ACI interval is incomplete")
        covered = issue.interval_lower_mm <= actual <= issue.interval_upper_mm
        miss = 0.0 if covered else 1.0
        alpha = _clip_alpha(
            state.alpha + ACI_GAMMA_V1 * (TARGET_ALPHA_V1 - miss)
        )
    else:
        covered = None
        alpha = state.alpha
    updated_state = AciState(
        history_count=state.history_count + 1,
        absolute_residuals=(
            *state.absolute_residuals,
            absolute_residual,
        )[-MAXIMUM_HISTORY_V1:],
        alpha=alpha,
        reset_reason="none",
    )
    return AciReveal(
        actual_mm=actual,
        signed_residual_mm=signed_residual,
        absolute_residual_mm=absolute_residual,
        interval_covered=covered,
        alpha_after_update=alpha,
        updated_state_sha256=state_sha256(updated_state),
        updated_state=updated_state,
    )


def new_agaci_state(
    gamma_grid: Sequence[Real],
    *,
    reset_reason: str = "initial_fold_start",
) -> AgaciState:
    """Construct a fresh EWA AgACI-variant state for a fixed gamma grid."""

    return AgaciState(gamma_grid=tuple(gamma_grid), reset_reason=reset_reason)


def _ewa_weights(
    losses: tuple[float, ...], active_update_count: int
) -> tuple[tuple[float, ...], float]:
    count = len(losses)
    eta = math.sqrt(8.0 * math.log(count) / (active_update_count + 1))
    if active_update_count == 0:
        return (1.0 / count,) * count, eta
    log_weights = tuple(-eta * loss for loss in losses)
    maximum = max(log_weights)
    exponentials = tuple(math.exp(value - maximum) for value in log_weights)
    total = sum(exponentials)
    return tuple(value / total for value in exponentials), eta


def _weight_entropy(weights: tuple[float, ...]) -> float:
    return -sum(weight * math.log(weight) for weight in weights if weight > 0.0)


def issue_agaci(state: AgaciState, point_forecast_mm: Real) -> AgaciIssue:
    """Issue EWA endpoints from past normalized pinball losses only."""

    if not isinstance(state, AgaciState):
        raise CalibrationChallengerError("state must be AgaciState")
    point = _finite_float(point_forecast_mm, "point_forecast_mm")
    lower_weights, eta = _ewa_weights(
        state.lower_cumulative_normalized_pinball_loss,
        state.active_update_count,
    )
    upper_weights, upper_eta = _ewa_weights(
        state.upper_cumulative_normalized_pinball_loss,
        state.active_update_count,
    )
    if eta != upper_eta:
        raise CalibrationChallengerError("AgACI side learning rates diverged")
    effective_gamma_lower = sum(
        weight * gamma for weight, gamma in zip(lower_weights, state.gamma_grid)
    )
    effective_gamma_upper = sum(
        weight * gamma for weight, gamma in zip(upper_weights, state.gamma_grid)
    )
    common = {
        "state_before_sha256": state_sha256(state),
        "state_reset_reason": state.reset_reason,
        "history_count": state.history_count,
        "active_update_count": state.active_update_count,
        "point_forecast_mm": point,
        "eta": eta,
        "gamma_grid": state.gamma_grid,
        "expert_alphas": state.expert_alphas,
        "lower_weights": lower_weights,
        "upper_weights": upper_weights,
        "effective_gamma_lower": effective_gamma_lower,
        "effective_gamma_upper": effective_gamma_upper,
        "lower_weight_entropy": _weight_entropy(lower_weights),
        "upper_weight_entropy": _weight_entropy(upper_weights),
    }
    if state.history_count < MINIMUM_HISTORY_V1:
        return AgaciIssue(
            **common,
            interval_status="abstain_rewarm",
            failure_reason=None,
            expert_q_mm=None,
            expert_lower_bounds_mm=None,
            expert_upper_bounds_mm=None,
            interval_lower_mm=None,
            interval_upper_mm=None,
        )
    expert_q = tuple(
        _finite_sample_higher(state.absolute_residuals, alpha)
        for alpha in state.expert_alphas
    )
    expert_lower = tuple(point - qhat for qhat in expert_q)
    expert_upper = tuple(point + qhat for qhat in expert_q)
    if not all(
        math.isfinite(value) for value in (*expert_lower, *expert_upper)
    ):
        return AgaciIssue(
            **common,
            interval_status="fail_closed",
            failure_reason="nonfinite_bounds",
            expert_q_mm=expert_q,
            expert_lower_bounds_mm=None,
            expert_upper_bounds_mm=None,
            interval_lower_mm=None,
            interval_upper_mm=None,
        )
    lower = sum(
        weight * bound for weight, bound in zip(lower_weights, expert_lower)
    )
    upper = sum(
        weight * bound for weight, bound in zip(upper_weights, expert_upper)
    )
    if not math.isfinite(lower) or not math.isfinite(upper):
        reason = "nonfinite_bounds"
    elif lower > upper:
        reason = "crossing_bounds"
    else:
        reason = None
    return AgaciIssue(
        **common,
        interval_status="interval_available" if reason is None else "fail_closed",
        failure_reason=reason,
        expert_q_mm=expert_q,
        expert_lower_bounds_mm=expert_lower,
        expert_upper_bounds_mm=expert_upper,
        interval_lower_mm=lower if reason is None else None,
        interval_upper_mm=upper if reason is None else None,
    )


def _pinball_loss(actual: float, quantile: float, tau: float) -> float:
    residual = actual - quantile
    return tau * residual if residual >= 0.0 else (tau - 1.0) * residual


def _normalize_daily_losses(losses: tuple[float, ...]) -> tuple[float, ...]:
    maximum = max(losses)
    if maximum == 0.0:
        return (0.0,) * len(losses)
    return tuple(loss / maximum for loss in losses)


def reveal_agaci(
    state: AgaciState,
    issue: AgaciIssue,
    actual_mm: Real,
) -> AgaciReveal:
    """Score issued expert endpoints, then update losses and expert alphas."""

    if not isinstance(state, AgaciState) or not isinstance(issue, AgaciIssue):
        raise CalibrationChallengerError(
            "state/issue must be AgaciState/AgaciIssue"
        )
    if issue != issue_agaci(state, issue.point_forecast_mm):
        raise CalibrationChallengerError("issue does not match the supplied state")
    actual = _finite_float(actual_mm, "actual_mm")
    signed_residual = actual - issue.point_forecast_mm
    absolute_residual = abs(signed_residual)
    lower_cumulative = state.lower_cumulative_normalized_pinball_loss
    upper_cumulative = state.upper_cumulative_normalized_pinball_loss
    alphas = state.expert_alphas
    active_count = state.active_update_count

    if issue.interval_status == "interval_available":
        if (
            issue.expert_lower_bounds_mm is None
            or issue.expert_upper_bounds_mm is None
            or issue.interval_lower_mm is None
            or issue.interval_upper_mm is None
        ):
            raise CalibrationChallengerError("available AgACI interval is incomplete")
        lower_loss = tuple(
            _pinball_loss(actual, bound, SPCI_LOWER_TAU_V1)
            for bound in issue.expert_lower_bounds_mm
        )
        upper_loss = tuple(
            _pinball_loss(actual, bound, SPCI_UPPER_TAU_V1)
            for bound in issue.expert_upper_bounds_mm
        )
        lower_normalized = _normalize_daily_losses(lower_loss)
        upper_normalized = _normalize_daily_losses(upper_loss)
        lower_cumulative = tuple(
            previous + current
            for previous, current in zip(lower_cumulative, lower_normalized)
        )
        upper_cumulative = tuple(
            previous + current
            for previous, current in zip(upper_cumulative, upper_normalized)
        )
        expert_covered = tuple(
            lower <= actual <= upper
            for lower, upper in zip(
                issue.expert_lower_bounds_mm,
                issue.expert_upper_bounds_mm,
            )
        )
        alphas = tuple(
            _clip_alpha(
                alpha
                + gamma
                * (TARGET_ALPHA_V1 - (0.0 if covered else 1.0))
            )
            for alpha, gamma, covered in zip(
                state.expert_alphas,
                state.gamma_grid,
                expert_covered,
            )
        )
        active_count += 1
        covered = issue.interval_lower_mm <= actual <= issue.interval_upper_mm
    else:
        lower_loss = None
        upper_loss = None
        lower_normalized = None
        upper_normalized = None
        expert_covered = None
        covered = None

    updated_state = AgaciState(
        gamma_grid=state.gamma_grid,
        history_count=state.history_count + 1,
        absolute_residuals=(
            *state.absolute_residuals,
            absolute_residual,
        )[-MAXIMUM_HISTORY_V1:],
        expert_alphas=alphas,
        lower_cumulative_normalized_pinball_loss=lower_cumulative,
        upper_cumulative_normalized_pinball_loss=upper_cumulative,
        active_update_count=active_count,
        reset_reason="none",
    )
    return AgaciReveal(
        actual_mm=actual,
        signed_residual_mm=signed_residual,
        absolute_residual_mm=absolute_residual,
        interval_covered=covered,
        expert_interval_covered=expert_covered,
        lower_pinball_loss=lower_loss,
        upper_pinball_loss=upper_loss,
        lower_normalized_pinball_loss=lower_normalized,
        upper_normalized_pinball_loss=upper_normalized,
        expert_alphas_after_update=alphas,
        active_update_count_after_update=active_count,
        updated_state_sha256=state_sha256(updated_state),
        updated_state=updated_state,
    )


def new_spci_state(
    settings: SpciSettings | None = None,
    *,
    reset_reason: str = "initial_fold_start",
    **settings_kwargs: Any,
) -> SpciState:
    """Construct a fresh SPCI state from a settings object or settings kwargs."""

    if settings is not None and settings_kwargs:
        raise CalibrationChallengerError(
            "pass either settings or SPCI settings kwargs, not both"
        )
    resolved = settings if settings is not None else SpciSettings(**settings_kwargs)
    if not isinstance(resolved, SpciSettings):
        raise CalibrationChallengerError("settings must be SpciSettings")
    return SpciState(settings=resolved, reset_reason=reset_reason)


def _resolve_spci_settings(
    state: SpciState,
    settings: SpciSettings | None,
    settings_kwargs: dict[str, Any],
) -> SpciSettings:
    if settings is not None and settings_kwargs:
        raise CalibrationChallengerError(
            "pass either settings or SPCI settings kwargs, not both"
        )
    if settings is not None:
        resolved = settings
    elif settings_kwargs:
        values = _spci_settings_payload(state.settings)
        values.update(settings_kwargs)
        resolved = SpciSettings(**values)
    else:
        resolved = state.settings
    if not isinstance(resolved, SpciSettings) or resolved != state.settings:
        raise CalibrationChallengerError(
            "issue settings must exactly match the settings hashed in state"
        )
    return resolved


def _qrf_distribution(
    features: np.ndarray,
    targets: np.ndarray,
    query: np.ndarray,
    settings: SpciSettings,
) -> tuple[np.ndarray, np.ndarray]:
    forest = RandomForestRegressor(
        n_estimators=settings.n_estimators,
        max_depth=settings.max_depth,
        min_samples_leaf=settings.min_samples_leaf,
        max_features=settings.max_features,
        bootstrap=settings.bootstrap,
        n_jobs=settings.n_jobs,
        random_state=settings.random_state,
    )
    forest.fit(features, targets)
    weights = np.zeros(len(targets), dtype=float)
    for tree, bootstrap_indices in zip(
        forest.estimators_, forest.estimators_samples_
    ):
        sample_indices = np.asarray(bootstrap_indices, dtype=int)
        query_leaf = tree.apply(query.reshape(1, -1))[0]
        sample_leaves = tree.apply(features[sample_indices])
        members = sample_indices[sample_leaves == query_leaf]
        if len(members) == 0:
            raise CalibrationChallengerError("QRF query leaf has no bootstrap sample")
        per_occurrence = 1.0 / (settings.n_estimators * len(members))
        np.add.at(weights, members, per_occurrence)
    total = float(weights.sum())
    if (
        not np.all(np.isfinite(weights))
        or np.any(weights < 0.0)
        or not math.isfinite(total)
        or total <= 0.0
    ):
        raise CalibrationChallengerError("QRF produced invalid sample weights")
    weights /= total
    return targets, weights


def _qrf_weighted_quantile(
    values: np.ndarray,
    weights: np.ndarray,
    probability: float,
) -> float:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    probability = _finite_float(probability, "probability")
    if values.ndim != 1 or weights.ndim != 1 or len(values) != len(weights):
        raise CalibrationChallengerError(
            "weighted quantile values and weights must be aligned vectors"
        )
    if not 0.0 <= probability <= 1.0:
        raise CalibrationChallengerError("probability must be in [0, 1]")
    if np.any(~np.isfinite(weights)) or np.any(weights < 0.0):
        raise CalibrationChallengerError("weighted quantile weights are invalid")
    support = weights > 0.0
    if not np.any(support):
        raise CalibrationChallengerError("weighted quantile support is empty")
    supported_values = values[support]
    supported_weights = weights[support]
    if np.any(~np.isfinite(supported_values)):
        raise CalibrationChallengerError(
            "positive-weight quantile values must be finite"
        )
    total = float(supported_weights.sum())
    if not math.isfinite(total) or total <= 0.0:
        raise CalibrationChallengerError("weighted quantile mass is invalid")
    supported_weights = supported_weights / total
    order = np.argsort(supported_values, kind="stable")
    ordered_values = supported_values[order]
    ordered_weights = supported_weights[order]
    if probability == 0.0:
        return float(ordered_values[0])
    if probability == 1.0:
        return float(ordered_values[-1])
    cumulative = np.cumsum(ordered_weights)
    index = int(np.searchsorted(cumulative, probability, side="left"))
    index = min(max(index, 0), len(ordered_values) - 1)
    return float(ordered_values[index])


def _failed_spci_issue(
    state: SpciState,
    point: float,
    settings: SpciSettings,
    pair_count: int,
    reason: str,
) -> SpciIssue:
    return SpciIssue(
        state_before_sha256=state_sha256(state),
        state_reset_reason=state.reset_reason,
        history_count=state.history_count,
        residual_window_count=len(state.signed_residuals),
        training_pair_count=pair_count,
        interval_status="fail_closed",
        failure_reason=reason,
        point_forecast_mm=point,
        settings=settings,
        selected_beta=None,
        lower_residual_quantile_mm=None,
        upper_residual_quantile_mm=None,
        interval_lower_mm=None,
        interval_upper_mm=None,
    )


def issue_spci(
    state: SpciState,
    point_forecast_mm: Real,
    settings: SpciSettings | None = None,
    **settings_kwargs: Any,
) -> SpciIssue:
    """Fit a deterministic causal QRF and issue the narrowest beta interval."""

    if not isinstance(state, SpciState):
        raise CalibrationChallengerError("state must be SpciState")
    point = _finite_float(point_forecast_mm, "point_forecast_mm")
    resolved = _resolve_spci_settings(state, settings, settings_kwargs)
    pair_count = max(len(state.signed_residuals) - resolved.lag, 0)
    if pair_count < resolved.minimum_pairs:
        return SpciIssue(
            state_before_sha256=state_sha256(state),
            state_reset_reason=state.reset_reason,
            history_count=state.history_count,
            residual_window_count=len(state.signed_residuals),
            training_pair_count=pair_count,
            interval_status="abstain_rewarm",
            failure_reason=None,
            point_forecast_mm=point,
            settings=resolved,
            selected_beta=None,
            lower_residual_quantile_mm=None,
            upper_residual_quantile_mm=None,
            interval_lower_mm=None,
            interval_upper_mm=None,
        )

    residuals = state.signed_residuals
    lag = resolved.lag
    features = np.asarray(
        [
            tuple(reversed(residuals[index : index + lag]))
            for index in range(pair_count)
        ],
        dtype=float,
    )
    targets = np.asarray(residuals[lag:], dtype=float)
    # SPCI Eq. 13 orders every lag vector latest-to-oldest:
    # [e_{j+w-1}, ..., e_j], including the live query prefix.
    query = np.asarray(tuple(reversed(residuals[-lag:])), dtype=float)
    try:
        values, weights = _qrf_distribution(
            features, targets, query, resolved
        )
        candidates: list[tuple[float, float, float, float]] = []
        for beta in sorted(resolved.beta_grid):
            lower_q = _qrf_weighted_quantile(values, weights, beta)
            upper_q = _qrf_weighted_quantile(
                values,
                weights,
                1.0 - TARGET_ALPHA_V1 + beta,
            )
            if not math.isfinite(lower_q) or not math.isfinite(upper_q):
                return _failed_spci_issue(
                    state, point, resolved, pair_count, "nonfinite_quantiles"
                )
            if lower_q > upper_q:
                return _failed_spci_issue(
                    state, point, resolved, pair_count, "crossing_quantiles"
                )
            candidates.append((upper_q - lower_q, beta, lower_q, upper_q))
    except Exception:
        return _failed_spci_issue(
            state, point, resolved, pair_count, "fit_or_qrf_exception"
        )
    _, selected_beta, lower_q, upper_q = min(
        candidates,
        key=lambda candidate: (candidate[0], candidate[1]),
    )
    lower = point + lower_q
    upper = point + upper_q
    if not math.isfinite(lower) or not math.isfinite(upper):
        return _failed_spci_issue(
            state, point, resolved, pair_count, "nonfinite_bounds"
        )
    if lower > upper:
        return _failed_spci_issue(
            state, point, resolved, pair_count, "crossing_bounds"
        )
    return SpciIssue(
        state_before_sha256=state_sha256(state),
        state_reset_reason=state.reset_reason,
        history_count=state.history_count,
        residual_window_count=len(residuals),
        training_pair_count=pair_count,
        interval_status="interval_available",
        failure_reason=None,
        point_forecast_mm=point,
        settings=resolved,
        selected_beta=selected_beta,
        lower_residual_quantile_mm=lower_q,
        upper_residual_quantile_mm=upper_q,
        interval_lower_mm=lower,
        interval_upper_mm=upper,
    )


def reveal_spci(
    state: SpciState,
    issue: SpciIssue,
    actual_mm: Real,
) -> SpciReveal:
    """Reveal an outcome after issue and append its signed residual."""

    if not isinstance(state, SpciState) or not isinstance(issue, SpciIssue):
        raise CalibrationChallengerError("state/issue must be SpciState/SpciIssue")
    expected = issue_spci(state, issue.point_forecast_mm, issue.settings)
    if issue != expected:
        raise CalibrationChallengerError("issue does not match the supplied state")
    actual = _finite_float(actual_mm, "actual_mm")
    signed_residual = actual - issue.point_forecast_mm
    if issue.interval_status == "interval_available":
        if issue.interval_lower_mm is None or issue.interval_upper_mm is None:
            raise CalibrationChallengerError("available SPCI interval is incomplete")
        covered = issue.interval_lower_mm <= actual <= issue.interval_upper_mm
    else:
        covered = None
    updated_state = SpciState(
        settings=state.settings,
        history_count=state.history_count + 1,
        signed_residuals=(
            *state.signed_residuals,
            signed_residual,
        )[-MAXIMUM_HISTORY_V1:],
        reset_reason="none",
    )
    return SpciReveal(
        actual_mm=actual,
        signed_residual_mm=signed_residual,
        absolute_residual_mm=abs(signed_residual),
        interval_covered=covered,
        updated_state_sha256=state_sha256(updated_state),
        updated_state=updated_state,
    )


__all__ = [
    "ACI_ALGORITHM_V1",
    "ACI_GAMMA_V1",
    "AGACI_ALGORITHM_V1",
    "AciIssue",
    "AciReveal",
    "AciState",
    "AgaciIssue",
    "AgaciReveal",
    "AgaciState",
    "CalibrationChallengerError",
    "INITIAL_ALPHA_V1",
    "MAXIMUM_ALPHA_V1",
    "MAXIMUM_HISTORY_V1",
    "MINIMUM_ALPHA_V1",
    "MINIMUM_HISTORY_V1",
    "RESET_REASONS_V1",
    "SPCI_ALGORITHM_V1",
    "SPCI_LAG_V1",
    "SPCI_MINIMUM_PAIRS_V1",
    "STATE_CODEC_VERSION_V1",
    "SpciIssue",
    "SpciReveal",
    "SpciSettings",
    "SpciState",
    "TARGET_COVERAGE_V1",
    "encode_state_canonical_json",
    "issue_aci",
    "issue_agaci",
    "issue_spci",
    "new_aci_state",
    "new_agaci_state",
    "new_spci_state",
    "reveal_aci",
    "reveal_agaci",
    "reveal_spci",
    "state_sha256",
]
