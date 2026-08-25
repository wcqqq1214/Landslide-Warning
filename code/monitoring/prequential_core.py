"""Pure, versioned online mathematics for the Ootang prequential monitor.

The module deliberately has no filesystem, clock, NumPy, or pandas dependency.
Callers own ordering, date barriers, persistence, and append-only ledgers.  The
functions here operate on one immutable station state at a time and expose the
same v1 mathematics as :mod:`monitoring.ootang_prequential_monitor`.

Unavailable issue/reveal quantities are represented by ``None``.  NaN and
infinity are never accepted as inputs or persisted in station state.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
from numbers import Integral, Real
from typing import Any, ClassVar, Mapping, Sequence


CORE_VERSION_V1 = "ootang-prequential-core-v1"
STATE_CODEC_VERSION_V1 = "ootang-station-state-v1"
EXPERT_COUNT_V1 = 6
MINIMUM_HISTORY_V1 = 60
MAXIMUM_HISTORY_V1 = 180
TARGET_COVERAGE_V1 = 0.8
INITIAL_ALPHA_V1 = 0.2
ACI_GAMMA_V1 = 1.0 / 180.0
MINIMUM_ALPHA_V1 = 0.01
MAXIMUM_ALPHA_V1 = 0.5
DRIFT_DELTA_V1 = 0.002
DRIFT_MINIMUM_SUBWINDOW_V1 = 30
DRIFT_MAXIMUM_HISTORY_V1 = 180

SITE_STATIONS_V1 = (
    "ATU1",
    "ATU2",
    "ATU3",
    "ATU4",
    "ATU5",
    "MJ1",
    "MJ3",
    "MJ9",
)
SPATIAL_BLOCKS_V1 = (
    ("O1", ("MJ9", "MJ1", "MJ3")),
    ("O2", ("ATU4", "ATU5", "ATU3")),
    ("O3", ("ATU2", "ATU1")),
)

_RESET_REASONS_V1 = frozenset(
    {
        "none",
        "initial_fold_start",
        "live_epoch_start",
        "fold_change",
        "drift_detected_previous_date",
    }
)
_STATE_KEYS_V1 = frozenset(
    {
        "expert_count",
        "initial_alpha",
        "cumulative_loss",
        "history_count",
        "absolute_residuals",
        "underprediction_residuals",
        "drift_values",
        "alpha",
        "next_issue_reset_reason",
    }
)


class PrequentialCoreError(ValueError):
    """Raised when a v1 state, issue, reveal, or site input is invalid."""


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise PrequentialCoreError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise PrequentialCoreError(
            f"{name} must be a finite real number"
        ) from exc
    if not math.isfinite(result):
        raise PrequentialCoreError(f"{name} must be finite")
    return result


def _nonnegative_float(value: object, name: str) -> float:
    result = _finite_float(value, name)
    if result < 0.0:
        raise PrequentialCoreError(f"{name} must be nonnegative")
    return result


def _bounded_float(value: object, name: str, lower: float, upper: float) -> float:
    result = _finite_float(value, name)
    if not lower <= result <= upper:
        raise PrequentialCoreError(f"{name} must be in [{lower}, {upper}]")
    return result


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise PrequentialCoreError(f"{name} must be an integer")
    return int(value)


def _numeric_tuple(
    values: object,
    name: str,
    *,
    nonnegative: bool = False,
    bounds: tuple[float, float] | None = None,
) -> tuple[float, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(
        values, Sequence
    ):
        raise PrequentialCoreError(f"{name} must be a numeric sequence")
    result: list[float] = []
    for index, value in enumerate(values):
        item_name = f"{name}[{index}]"
        if bounds is not None:
            number = _bounded_float(value, item_name, *bounds)
        elif nonnegative:
            number = _nonnegative_float(value, item_name)
        else:
            number = _finite_float(value, item_name)
        result.append(number)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class StationStateV1:
    """Immutable complete online state for one station and model version."""

    codec_version: ClassVar[str] = STATE_CODEC_VERSION_V1
    cumulative_loss: tuple[float, ...] = (0.0,) * EXPERT_COUNT_V1
    history_count: int = 0
    absolute_residuals: tuple[float, ...] = ()
    underprediction_residuals: tuple[float, ...] = ()
    drift_values: tuple[float, ...] = ()
    alpha: float = INITIAL_ALPHA_V1
    next_issue_reset_reason: str = "initial_fold_start"

    def __post_init__(self) -> None:
        cumulative_loss = _numeric_tuple(
            self.cumulative_loss, "cumulative_loss", nonnegative=True
        )
        if len(cumulative_loss) != EXPERT_COUNT_V1:
            raise PrequentialCoreError(
                f"cumulative_loss must contain exactly {EXPERT_COUNT_V1} experts"
            )
        history_count = _integer(self.history_count, "history_count")
        if history_count < 0:
            raise PrequentialCoreError("history_count must be nonnegative")
        absolute_residuals = _numeric_tuple(
            self.absolute_residuals,
            "absolute_residuals",
            nonnegative=True,
        )
        underprediction_residuals = _numeric_tuple(
            self.underprediction_residuals,
            "underprediction_residuals",
            nonnegative=True,
        )
        drift_values = _numeric_tuple(
            self.drift_values, "drift_values", bounds=(0.0, 1.0)
        )
        expected_window_length = min(history_count, MAXIMUM_HISTORY_V1)
        lengths = {
            len(absolute_residuals),
            len(underprediction_residuals),
            len(drift_values),
        }
        if lengths != {expected_window_length}:
            raise PrequentialCoreError(
                "all residual/drift windows must have length "
                "min(history_count, 180)"
            )
        alpha = _bounded_float(
            self.alpha, "alpha", MINIMUM_ALPHA_V1, MAXIMUM_ALPHA_V1
        )
        if (
            not isinstance(self.next_issue_reset_reason, str)
            or self.next_issue_reset_reason not in _RESET_REASONS_V1
        ):
            raise PrequentialCoreError(
                "next_issue_reset_reason is not a supported v1 reason"
            )
        object.__setattr__(self, "cumulative_loss", cumulative_loss)
        object.__setattr__(self, "history_count", history_count)
        object.__setattr__(self, "absolute_residuals", absolute_residuals)
        object.__setattr__(
            self, "underprediction_residuals", underprediction_residuals
        )
        object.__setattr__(self, "drift_values", drift_values)
        object.__setattr__(self, "alpha", alpha)


@dataclass(frozen=True, slots=True)
class StationIssueV1:
    """Pure issue-time result derived solely from past station state."""

    core_version: ClassVar[str] = CORE_VERSION_V1
    state_before_sha256: str
    state_reset_reason: str
    history_count: int
    forecast_action: str
    uncertainty_action: str
    eta: float
    experts_mm: tuple[float, ...]
    weights: tuple[float, ...]
    point_forecast_mm: float
    fallback_persistence_mm: float
    aci_alpha: float
    conformal_history_count: int
    conformal_q_mm: float | None
    interval_lower_mm: float | None
    interval_upper_mm: float | None


@dataclass(frozen=True, slots=True)
class StationRevealV1:
    """Outcome-time result plus pre-reset and effective next station state."""

    core_version: ClassVar[str] = CORE_VERSION_V1
    actual_mm: float
    point_absolute_error_mm: float
    persistence_absolute_error_mm: float
    interval_covered: bool | None
    interval_width_mm: float | None
    underprediction_residual_mm: float
    anomaly_p_value: float | None
    anomaly_score: float | None
    absolute_residual_surprise_rank: float
    drift_detected: bool
    drift_cut_index: int | None
    aci_alpha_after_update: float
    history_count_after_update: int
    state_reset_after_update: bool
    updated_state_sha256: str
    state_after_sha256: str
    updated_state: StationStateV1
    next_state: StationStateV1


@dataclass(frozen=True, slots=True)
class BlockAggregateV1:
    """One spatial block's available maximum anomaly score."""

    name: str
    available_station_count: int
    expected_station_count: int
    contributor_station: str
    maximum_anomaly_score: float | None


@dataclass(frozen=True, slots=True)
class SiteAggregateV1:
    """O1/O2/O3 block aggregation for one fully revealed target date."""

    core_version: ClassVar[str] = CORE_VERSION_V1
    available_station_score_count: int
    abstained_station_score_count: int
    blocks: tuple[BlockAggregateV1, ...]
    cross_block_min_of_block_max_anomaly_score: float | None
    site_score_status: str

    def block(self, name: str) -> BlockAggregateV1:
        """Return a named O1/O2/O3 summary, rejecting unknown block names."""

        for block in self.blocks:
            if block.name == name:
                return block
        raise PrequentialCoreError(f"unknown spatial block: {name}")


def new_station_state_v1(
    *, reset_reason: str = "initial_fold_start"
) -> StationStateV1:
    """Create a fresh station state for a fold/model-version or drift reset."""

    return StationStateV1(next_issue_reset_reason=reset_reason)


def _state_payload_v1(state: StationStateV1) -> dict[str, Any]:
    if not isinstance(state, StationStateV1):
        raise PrequentialCoreError("state must be StationStateV1")
    return {
        # These two constants preserve byte-for-byte compatibility with the E1
        # state hashes while versioning the Python API and codec explicitly.
        "expert_count": EXPERT_COUNT_V1,
        "initial_alpha": INITIAL_ALPHA_V1,
        "cumulative_loss": list(state.cumulative_loss),
        "history_count": state.history_count,
        "absolute_residuals": list(state.absolute_residuals),
        "underprediction_residuals": list(state.underprediction_residuals),
        "drift_values": list(state.drift_values),
        "alpha": state.alpha,
        "next_issue_reset_reason": state.next_issue_reset_reason,
    }


def encode_station_state_v1(state: StationStateV1) -> bytes:
    """Encode state as the canonical compact JSON bytes used by E1 hashes."""

    return json.dumps(
        _state_payload_v1(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_json_constant(value: str) -> None:
    raise PrequentialCoreError(f"forbidden JSON numeric constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PrequentialCoreError(f"duplicate station-state key: {key}")
        result[key] = value
    return result


def decode_station_state_v1(payload: bytes | str) -> StationStateV1:
    """Decode only canonical v1 state JSON; malformed/tampered state fails closed."""

    if isinstance(payload, str):
        try:
            raw = payload.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise PrequentialCoreError("station state is not valid UTF-8") from exc
    elif isinstance(payload, bytes):
        raw = payload
    else:
        raise PrequentialCoreError("station-state payload must be bytes or text")
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrequentialCoreError("invalid station-state JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != _STATE_KEYS_V1:
        raise PrequentialCoreError("station-state schema does not match v1")
    if parsed["expert_count"] != EXPERT_COUNT_V1:
        raise PrequentialCoreError("station-state expert_count does not match v1")
    if parsed["initial_alpha"] != INITIAL_ALPHA_V1:
        raise PrequentialCoreError("station-state initial_alpha does not match v1")
    state = StationStateV1(
        cumulative_loss=parsed["cumulative_loss"],
        history_count=parsed["history_count"],
        absolute_residuals=parsed["absolute_residuals"],
        underprediction_residuals=parsed["underprediction_residuals"],
        drift_values=parsed["drift_values"],
        alpha=parsed["alpha"],
        next_issue_reset_reason=parsed["next_issue_reset_reason"],
    )
    if raw != encode_station_state_v1(state):
        raise PrequentialCoreError("station-state JSON is not canonical v1")
    return state


def station_state_sha256_v1(state: StationStateV1) -> str:
    """Return the E1-compatible SHA-256 of canonical v1 station state."""

    return hashlib.sha256(encode_station_state_v1(state)).hexdigest()


def verify_station_state_v1(
    payload: bytes | str, expected_sha256: str
) -> StationStateV1:
    """Decode canonical state and verify its externally recorded digest."""

    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise PrequentialCoreError("expected state SHA-256 must be lowercase 64-hex")
    state = decode_station_state_v1(payload)
    actual = station_state_sha256_v1(state)
    if not hmac.compare_digest(actual, expected_sha256):
        raise PrequentialCoreError("station-state SHA-256 mismatch")
    return state


def _finite_sample_higher_v1(values: tuple[float, ...], alpha: float) -> float:
    ordered = sorted(values)
    rank = math.ceil((len(ordered) + 1) * (1.0 - alpha))
    rank = min(max(rank, 1), len(ordered))
    return ordered[rank - 1]


def _expert_weights_v1(state: StationStateV1) -> tuple[tuple[float, ...], float]:
    issue_index = state.history_count + 1
    eta = math.sqrt(8.0 * math.log(EXPERT_COUNT_V1) / issue_index)
    log_weights = tuple(-eta * loss for loss in state.cumulative_loss)
    maximum = max(log_weights)
    exponentials = tuple(math.exp(value - maximum) for value in log_weights)
    total = sum(exponentials)
    return tuple(value / total for value in exponentials), eta


def issue_station(
    state: StationStateV1, experts_mm: Sequence[Real]
) -> StationIssueV1:
    """Issue a v1 forecast without mutating or advancing ``state``."""

    if not isinstance(state, StationStateV1):
        raise PrequentialCoreError("state must be StationStateV1")
    experts = _numeric_tuple(experts_mm, "experts_mm")
    if len(experts) != EXPERT_COUNT_V1:
        raise PrequentialCoreError(
            f"experts_mm must contain exactly {EXPERT_COUNT_V1} values"
        )
    weights, eta = _expert_weights_v1(state)
    point = sum(weight * expert for weight, expert in zip(weights, experts))
    ready = state.history_count >= MINIMUM_HISTORY_V1
    qhat = (
        _finite_sample_higher_v1(state.absolute_residuals, state.alpha)
        if ready
        else None
    )
    lower = point - qhat if qhat is not None else None
    upper = point + qhat if qhat is not None else None
    return StationIssueV1(
        state_before_sha256=station_state_sha256_v1(state),
        state_reset_reason=state.next_issue_reset_reason,
        history_count=state.history_count,
        forecast_action="point_forecast" if ready else "abstain",
        uncertainty_action="interval_available" if ready else "abstain_rewarm",
        eta=eta,
        experts_mm=experts,
        weights=weights,
        point_forecast_mm=point,
        fallback_persistence_mm=experts[0],
        aci_alpha=state.alpha,
        conformal_history_count=len(state.absolute_residuals),
        conformal_q_mm=qhat,
        interval_lower_mm=lower,
        interval_upper_mm=upper,
    )


def _tail_p_value_v1(current: float, past: tuple[float, ...]) -> float:
    greater_equal = sum(value >= current for value in past)
    return (1.0 + greater_equal) / (1.0 + len(past))


def _numpy_pairwise_sum_v1(values: tuple[float, ...]) -> float:
    """Match NumPy's binary64 pairwise reduction used by E1 drift means."""

    length = len(values)
    if length < 8:
        result = -0.0
        for value in values:
            result += value
        return result
    if length <= 128:
        accumulators = list(values[:8])
        index = 8
        while index + 7 < length:
            for offset in range(8):
                accumulators[offset] += values[index + offset]
            index += 8
        result = (
            (accumulators[0] + accumulators[1])
            + (accumulators[2] + accumulators[3])
        ) + (
            (accumulators[4] + accumulators[5])
            + (accumulators[6] + accumulators[7])
        )
        while index < length:
            result += values[index]
            index += 1
        return result
    midpoint = length // 2
    midpoint -= midpoint % 8
    return _numpy_pairwise_sum_v1(values[:midpoint]) + _numpy_pairwise_sum_v1(
        values[midpoint:]
    )


def _mean_v1(values: tuple[float, ...]) -> float:
    return _numpy_pairwise_sum_v1(values) / len(values)


def _drift_cut_v1(values: tuple[float, ...]) -> int | None:
    length = len(values)
    if length < 2 * DRIFT_MINIMUM_SUBWINDOW_V1:
        return None
    log_term = math.log(4.0 * length / DRIFT_DELTA_V1)
    best: tuple[float, int] | None = None
    for cut in range(
        DRIFT_MINIMUM_SUBWINDOW_V1,
        length - DRIFT_MINIMUM_SUBWINDOW_V1 + 1,
    ):
        left = values[:cut]
        right = values[cut:]
        difference = abs(_mean_v1(left) - _mean_v1(right))
        bound = math.sqrt(
            0.5 * log_term * (1.0 / len(left) + 1.0 / len(right))
        )
        excess = difference - bound
        if excess > 0.0 and (best is None or excess > best[0]):
            best = (excess, cut)
    return None if best is None else best[1]


def reveal_station(
    state: StationStateV1, issue: StationIssueV1, actual_mm: Real
) -> StationRevealV1:
    """Reveal one outcome and return immutable updated/effective next states.

    Drift is evaluated after the reveal.  A detected drift preserves the
    pre-reset ``updated_state`` for audit, while ``next_state`` is fresh and
    therefore affects only the following issue.
    """

    if not isinstance(state, StationStateV1):
        raise PrequentialCoreError("state must be StationStateV1")
    if not isinstance(issue, StationIssueV1):
        raise PrequentialCoreError("issue must be StationIssueV1")
    expected_issue = issue_station(state, issue.experts_mm)
    if issue != expected_issue:
        raise PrequentialCoreError("issue does not match the supplied past state")
    actual = _finite_float(actual_mm, "actual_mm")
    point = issue.point_forecast_mm
    absolute_error = abs(actual - point)
    persistence_error = abs(actual - issue.experts_mm[0])
    underprediction = max(actual - point, 0.0)

    anomaly_ready = state.history_count >= MINIMUM_HISTORY_V1
    internal_anomaly_p = _tail_p_value_v1(
        underprediction, state.underprediction_residuals
    )
    internal_anomaly_score = -math.log10(internal_anomaly_p)
    anomaly_p = internal_anomaly_p if anomaly_ready else None
    anomaly_score = internal_anomaly_score if anomaly_ready else None
    absolute_tail_p = _tail_p_value_v1(
        absolute_error, state.absolute_residuals
    )
    surprise_rank = 1.0 - absolute_tail_p

    if issue.interval_lower_mm is not None:
        if issue.interval_upper_mm is None:
            raise PrequentialCoreError("issue interval is only partially available")
        covered = issue.interval_lower_mm <= actual <= issue.interval_upper_mm
        interval_width = issue.interval_upper_mm - issue.interval_lower_mm
        miss = 0.0 if covered else 1.0
        alpha = state.alpha + ACI_GAMMA_V1 * (
            (1.0 - TARGET_COVERAGE_V1) - miss
        )
        alpha = min(max(alpha, MINIMUM_ALPHA_V1), MAXIMUM_ALPHA_V1)
    else:
        if issue.interval_upper_mm is not None:
            raise PrequentialCoreError("issue interval is only partially available")
        covered = None
        interval_width = None
        alpha = state.alpha

    expert_errors = tuple(abs(expert - actual) for expert in issue.experts_mm)
    maximum_error = max(expert_errors)
    if maximum_error > 0.0:
        cumulative_loss = tuple(
            previous + error / maximum_error
            for previous, error in zip(state.cumulative_loss, expert_errors)
        )
    else:
        cumulative_loss = state.cumulative_loss
    history_count = state.history_count + 1
    absolute_residuals = (
        *state.absolute_residuals,
        absolute_error,
    )[-MAXIMUM_HISTORY_V1:]
    underprediction_residuals = (
        *state.underprediction_residuals,
        underprediction,
    )[-MAXIMUM_HISTORY_V1:]
    drift_values = (*state.drift_values, surprise_rank)[
        -DRIFT_MAXIMUM_HISTORY_V1:
    ]
    updated_state = StationStateV1(
        cumulative_loss=cumulative_loss,
        history_count=history_count,
        absolute_residuals=absolute_residuals,
        underprediction_residuals=underprediction_residuals,
        drift_values=drift_values,
        alpha=alpha,
        next_issue_reset_reason="none",
    )
    drift_cut = _drift_cut_v1(updated_state.drift_values)
    drift_detected = drift_cut is not None
    next_state = (
        new_station_state_v1(reset_reason="drift_detected_previous_date")
        if drift_detected
        else updated_state
    )
    return StationRevealV1(
        actual_mm=actual,
        point_absolute_error_mm=absolute_error,
        persistence_absolute_error_mm=persistence_error,
        interval_covered=covered,
        interval_width_mm=interval_width,
        underprediction_residual_mm=underprediction,
        anomaly_p_value=anomaly_p,
        anomaly_score=anomaly_score,
        absolute_residual_surprise_rank=surprise_rank,
        drift_detected=drift_detected,
        drift_cut_index=drift_cut,
        aci_alpha_after_update=updated_state.alpha,
        history_count_after_update=updated_state.history_count,
        state_reset_after_update=drift_detected,
        updated_state_sha256=station_state_sha256_v1(updated_state),
        state_after_sha256=station_state_sha256_v1(next_state),
        updated_state=updated_state,
        next_state=next_state,
    )


def aggregate_site_scores(
    station_anomaly_scores: Mapping[str, Real | None],
) -> SiteAggregateV1:
    """Apply the fixed v1 O1/O2/O3 max-then-min spatial aggregation."""

    if not isinstance(station_anomaly_scores, Mapping):
        raise PrequentialCoreError("station_anomaly_scores must be a mapping")
    if any(not isinstance(station, str) for station in station_anomaly_scores):
        raise PrequentialCoreError("station names must be text")
    unknown = set(station_anomaly_scores) - set(SITE_STATIONS_V1)
    if unknown:
        raise PrequentialCoreError(
            f"unknown v1 station anomaly scores: {sorted(unknown)}"
        )
    scores: dict[str, float] = {}
    for station, value in station_anomaly_scores.items():
        if value is not None:
            scores[station] = _finite_float(
                value, f"station_anomaly_scores[{station!r}]"
            )

    blocks: list[BlockAggregateV1] = []
    for name, members in SPATIAL_BLOCKS_V1:
        available = [(station, scores[station]) for station in members if station in scores]
        if available:
            contributor, score = max(available, key=lambda item: item[1])
        else:
            contributor, score = "", None
        blocks.append(
            BlockAggregateV1(
                name=name,
                available_station_count=len(available),
                expected_station_count=len(members),
                contributor_station=contributor,
                maximum_anomaly_score=score,
            )
        )

    available_count = len(scores)
    all_blocks_available = all(
        block.available_station_count > 0 for block in blocks
    )
    if available_count == len(SITE_STATIONS_V1):
        status = "complete_station_coverage"
    elif all_blocks_available:
        status = "available_subset_all_blocks"
    else:
        status = "abstain_spatial_incomplete"
    cross_block_score = (
        min(
            block.maximum_anomaly_score
            for block in blocks
            if block.maximum_anomaly_score is not None
        )
        if all_blocks_available
        else None
    )
    return SiteAggregateV1(
        available_station_score_count=available_count,
        abstained_station_score_count=len(SITE_STATIONS_V1) - available_count,
        blocks=tuple(blocks),
        cross_block_min_of_block_max_anomaly_score=cross_block_score,
        site_score_status=status,
    )


__all__ = [
    "ACI_GAMMA_V1",
    "CORE_VERSION_V1",
    "DRIFT_DELTA_V1",
    "DRIFT_MAXIMUM_HISTORY_V1",
    "DRIFT_MINIMUM_SUBWINDOW_V1",
    "EXPERT_COUNT_V1",
    "INITIAL_ALPHA_V1",
    "MAXIMUM_ALPHA_V1",
    "MAXIMUM_HISTORY_V1",
    "MINIMUM_ALPHA_V1",
    "MINIMUM_HISTORY_V1",
    "PrequentialCoreError",
    "SITE_STATIONS_V1",
    "SPATIAL_BLOCKS_V1",
    "STATE_CODEC_VERSION_V1",
    "StationIssueV1",
    "StationRevealV1",
    "StationStateV1",
    "SiteAggregateV1",
    "TARGET_COVERAGE_V1",
    "aggregate_site_scores",
    "decode_station_state_v1",
    "encode_station_state_v1",
    "issue_station",
    "new_station_state_v1",
    "reveal_station",
    "station_state_sha256_v1",
    "verify_station_state_v1",
]
