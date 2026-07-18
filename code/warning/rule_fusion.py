"""Draft, auditable per-station fusion of the four approved warning inputs.

The specified thesis uses supervised multinomial logistic regression.  The
project's approved replacement is transparent non-supervised fusion, so this
module implements a deliberately conservative draft candidate rather than
claiming to reproduce the thesis or to be a frozen formal warning path.

For each non-green severity, interval, velocity, and tangent-angle levels are
supporting evidence when they meet or exceed it.  A positive ``delta_v`` is a
qualitative escalation support at every severity; negative and near-zero
``delta_v`` do not support escalation.  Two supports are required.  A lone
anomalous input remains explicitly ``uncorroborated`` instead of being silently
relabelled green.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Integral
from typing import Any

from warning.levels import WarningLevel


ORDINAL_INDICATORS = ("interval", "velocity", "tangent_angle")
ALL_INDICATORS = ("interval", "velocity", "delta_v", "tangent_angle")
DELTA_V_STATES = ("negative", "near_zero", "positive")
INPUT_STATUSES = ("valid", "warmup", "invalid", "not_applicable")
DEFAULT_MINIMUM_SUPPORT = 2


@dataclass(frozen=True)
class StationFusionResult:
    """Per-station rule-fusion outcome with direct audit evidence."""

    status: str
    level: WarningLevel | None
    candidate_max_level: WarningLevel | None
    contributing_indicators: tuple[str, ...]
    reason: str
    input_statuses: tuple[tuple[str, str], ...]
    support_by_level: tuple[tuple[WarningLevel, tuple[str, ...]], ...]

    @property
    def color(self) -> str | None:
        """Return the formal five-color token only for a valid fused level."""

        return None if self.level is None else self.level.color

    def to_record(self) -> dict[str, Any]:
        """Return scalar fields for a future, auditable warning CSV."""

        support = {
            level.color: ";".join(indicators)
            for level, indicators in self.support_by_level
        }
        return {
            "fusion_status": self.status,
            "final_level": None if self.level is None else int(self.level),
            "final_color": self.color,
            "candidate_max_level": (
                None
                if self.candidate_max_level is None
                else int(self.candidate_max_level)
            ),
            "contributing_indicators": ";".join(self.contributing_indicators),
            "fusion_reason": self.reason,
            "input_statuses": ";".join(
                f"{name}:{status}" for name, status in self.input_statuses
            ),
            **{f"support_{level}": evidence for level, evidence in support.items()},
        }


def _normalise_level(value, *, name: str) -> WarningLevel:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name}_level must be a five-level integer")
    try:
        return WarningLevel(int(value))
    except ValueError as exc:
        raise ValueError(f"{name}_level must be between 0 and 4") from exc


def _normalise_delta_v_state(value) -> str:
    if not isinstance(value, str):
        raise ValueError("delta_v_state must be negative, near_zero, or positive")
    state = value.strip().lower()
    if state not in DELTA_V_STATES:
        raise ValueError("delta_v_state must be negative, near_zero, or positive")
    return state


def _normalise_input_statuses(
    values: Mapping[str, object],
    input_statuses: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...]:
    supplied = {} if input_statuses is None else dict(input_statuses)
    unknown = sorted(set(supplied).difference(ALL_INDICATORS))
    if unknown:
        raise ValueError(f"input_statuses has unknown indicators: {unknown}")

    statuses: list[tuple[str, str]] = []
    for name in ALL_INDICATORS:
        default = "valid" if values[name] is not None else "invalid"
        status = supplied.get(name, default)
        if status not in INPUT_STATUSES:
            raise ValueError(
                f"{name} status must be one of: {', '.join(INPUT_STATUSES)}"
            )
        statuses.append((name, status))
    return tuple(statuses)


def _nonvalid_result(
    *,
    status: str,
    input_statuses: tuple[tuple[str, str], ...],
) -> StationFusionResult:
    return StationFusionResult(
        status=status,
        level=None,
        candidate_max_level=None,
        contributing_indicators=(),
        reason=f"{status}_input",
        input_statuses=input_statuses,
        support_by_level=(),
    )


def fuse_station_indicators(
    *,
    interval_level: int | WarningLevel | None,
    velocity_level: int | WarningLevel | None,
    delta_v_state: str | None,
    tangent_angle_level: int | WarningLevel | None,
    input_statuses: Mapping[str, str] | None = None,
) -> StationFusionResult:
    """Fuse one station's four indicators with explicit missing-data handling.

    This is a draft candidate ``F`` only.  It requires all four inputs to be
    valid, making warm-up, invalid, and non-applicable conditions visible rather
    than silently dropping an indicator from the calculation.
    """

    values = {
        "interval": interval_level,
        "velocity": velocity_level,
        "delta_v": delta_v_state,
        "tangent_angle": tangent_angle_level,
    }
    statuses = _normalise_input_statuses(values, input_statuses)
    status_by_name = dict(statuses)
    if "invalid" in status_by_name.values():
        return _nonvalid_result(status="invalid", input_statuses=statuses)
    if "not_applicable" in status_by_name.values():
        return _nonvalid_result(status="not_applicable", input_statuses=statuses)
    if "warmup" in status_by_name.values():
        return _nonvalid_result(status="warmup", input_statuses=statuses)

    levels = {
        "interval": _normalise_level(interval_level, name="interval"),
        "velocity": _normalise_level(velocity_level, name="velocity"),
        "tangent_angle": _normalise_level(
            tangent_angle_level,
            name="tangent_angle",
        ),
    }
    delta_state = _normalise_delta_v_state(delta_v_state)
    support_by_level: list[tuple[WarningLevel, tuple[str, ...]]] = []
    for level in tuple(WarningLevel)[1:]:
        supporters = tuple(name for name in ORDINAL_INDICATORS if levels[name] >= level)
        if delta_state == "positive":
            supporters += ("delta_v",)
        support_by_level.append((level, supporters))

    corroborated = [
        (level, supporters)
        for level, supporters in support_by_level
        if len(supporters) >= DEFAULT_MINIMUM_SUPPORT
    ]
    candidate_max = max(levels.values())
    if corroborated:
        level, supporters = corroborated[-1]
        return StationFusionResult(
            status="valid",
            level=level,
            candidate_max_level=candidate_max,
            contributing_indicators=supporters,
            reason=f"corroborated_{level.color}",
            input_statuses=statuses,
            support_by_level=tuple(support_by_level),
        )

    if candidate_max == WarningLevel.GREEN and delta_state != "positive":
        return StationFusionResult(
            status="valid",
            level=WarningLevel.GREEN,
            candidate_max_level=candidate_max,
            contributing_indicators=(),
            reason="all_ordinal_green_with_nonpositive_delta_v",
            input_statuses=statuses,
            support_by_level=tuple(support_by_level),
        )
    return StationFusionResult(
        status="uncorroborated",
        level=None,
        candidate_max_level=candidate_max,
        contributing_indicators=(),
        reason="insufficient_corroboration",
        input_statuses=statuses,
        support_by_level=tuple(support_by_level),
    )


__all__ = [
    "ALL_INDICATORS",
    "DELTA_V_STATES",
    "DEFAULT_MINIMUM_SUPPORT",
    "INPUT_STATUSES",
    "ORDINAL_INDICATORS",
    "StationFusionResult",
    "fuse_station_indicators",
]
