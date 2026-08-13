"""Current v4 station-level evidence-family fusion for the Ootang prototype.

The module is intentionally limited to the active four-indicator rule:
interval, one velocity/tangent-angle kinematic family, and acceleration.  Raw
``delta_v`` remains an audit-only transition field and cannot cast a colour
vote.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Integral
from typing import Any

from warning.levels import WarningLevel

INPUT_STATUSES = ("valid", "warmup", "invalid", "not_applicable")
DELTA_V_STATES = ("negative", "near_zero", "positive")
_TREND_COMPONENT_BY_DELTA_V = {
    "negative": "delta_v_negative",
    "near_zero": "delta_v_near_zero",
    "positive": "delta_v_positive",
}
_TRANSITION_STATUS_BY_DELTA_V = {
    "negative": "velocity_decreasing",
    "near_zero": "velocity_near_steady",
    "positive": "velocity_increasing",
}


def _normalise_level(value: int | WarningLevel | None, *, name: str) -> WarningLevel:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{name}_level must be a five-level integer")
    try:
        return WarningLevel(int(value))
    except ValueError as exc:
        raise ValueError(f"{name}_level must be between 0 and 4") from exc


def _normalise_delta_v_state(value: str | None) -> str:
    if not isinstance(value, str):
        raise TypeError("delta_v_state must be negative, near_zero, or positive")
    state = value.strip().lower()
    if state not in DELTA_V_STATES:
        raise ValueError("delta_v_state must be negative, near_zero, or positive")
    return state


@dataclass(frozen=True)
class StationEvidenceResult:
    """A visible, assessable station candidate and its audit dimensions."""

    status: str
    candidate_level: WarningLevel | None
    kinematic_level: WarningLevel | None
    evidence_families: tuple[str, ...]
    acceleration_status: str | None
    trend_component: str | None
    transition_status: str | None
    evidence_consistency_status: str | None
    composite_signal: str | None
    confirmation_status: str
    reason: str
    input_statuses: tuple[tuple[str, str], ...]
    acceleration_level: WarningLevel | None = None
    acceleration_indicator_status: str | None = None
    acceleration_reason: str | None = None

    @property
    def candidate_color(self) -> str | None:
        return None if self.candidate_level is None else self.candidate_level.color

    @property
    def kinematic_color(self) -> str | None:
        return None if self.kinematic_level is None else self.kinematic_level.color

    @property
    def evidence_family_count(self) -> int:
        return len(self.evidence_families)

    def to_record(self) -> dict[str, Any]:
        """Return the materialized v4 audit fields and compatibility aliases."""

        return {
            "fusion_status": self.status,
            "final_level": (
                None if self.candidate_level is None else int(self.candidate_level)
            ),
            "final_color": self.candidate_color,
            "candidate_max_level": (
                None if self.candidate_level is None else int(self.candidate_level)
            ),
            "contributing_indicators": ";".join(self.evidence_families),
            "fusion_reason": self.reason,
            "input_statuses": ";".join(
                f"{name}:{status}" for name, status in self.input_statuses
            ),
            "station_assessment_status": self.status,
            "candidate_level": (
                None if self.candidate_level is None else int(self.candidate_level)
            ),
            "candidate_color": self.candidate_color,
            "kinematic_level": (
                None if self.kinematic_level is None else int(self.kinematic_level)
            ),
            "kinematic_color": self.kinematic_color,
            "evidence_families": ";".join(self.evidence_families),
            "evidence_family_count": self.evidence_family_count,
            "acceleration_status": self.acceleration_status,
            "acceleration_level": (
                None
                if self.acceleration_level is None
                else int(self.acceleration_level)
            ),
            "acceleration_color": (
                None
                if self.acceleration_level is None
                else self.acceleration_level.color
            ),
            "acceleration_indicator_status": self.acceleration_indicator_status,
            "acceleration_reason": self.acceleration_reason,
            "trend_component": self.trend_component,
            "transition_status": self.transition_status,
            "evidence_consistency_status": self.evidence_consistency_status,
            "composite_warning_signal": self.composite_signal,
            "station_confirmation_status": self.confirmation_status,
        }


def fuse_station_evidence_families_v4(
    *,
    interval_level: int | WarningLevel | None,
    velocity_level: int | WarningLevel | None,
    tangent_angle_level: int | WarningLevel | None,
    acceleration_level: int | WarningLevel | None,
    delta_v_state: str | None = None,
    input_statuses: Mapping[str, str] | None = None,
) -> StationEvidenceResult:
    """Fuse the active four ordinal indicators without double-counting.

    Velocity and tangent angle make one kinematic family.  Interval and
    acceleration are independent families.  The returned candidate is the
    highest ordinal level across those three families.
    """

    values = {
        "interval": interval_level,
        "velocity": velocity_level,
        "tangent_angle": tangent_angle_level,
        "acceleration": acceleration_level,
    }
    supplied = {} if input_statuses is None else dict(input_statuses)
    unknown = sorted(set(supplied).difference({*values, "delta_v"}))
    if unknown:
        raise ValueError(f"input_statuses has unknown indicators: {unknown}")
    statuses: list[tuple[str, str]] = []
    for name, value in values.items():
        status = supplied.get(name, "valid" if value is not None else "invalid")
        if status not in INPUT_STATUSES:
            raise ValueError(
                f"{name} status must be one of: {', '.join(INPUT_STATUSES)}"
            )
        statuses.append((name, status))
    if "delta_v" in supplied:
        delta_status = supplied["delta_v"]
        if delta_status not in INPUT_STATUSES:
            raise ValueError(
                "delta_v status must be one of: " + ", ".join(INPUT_STATUSES)
            )
        statuses.append(("delta_v", delta_status))

    nonvalid = next((status for _, status in statuses[:4] if status != "valid"), None)
    if nonvalid is not None:
        return StationEvidenceResult(
            status=nonvalid,
            candidate_level=None,
            kinematic_level=None,
            evidence_families=(),
            acceleration_status=None,
            trend_component=None,
            transition_status=None,
            evidence_consistency_status=None,
            composite_signal=None,
            confirmation_status=f"{nonvalid}_input",
            reason=f"{nonvalid}_input",
            input_statuses=tuple(statuses),
            acceleration_level=None,
            acceleration_indicator_status=nonvalid,
            acceleration_reason=f"source_{nonvalid}",
        )

    interval = _normalise_level(interval_level, name="interval")
    velocity = _normalise_level(velocity_level, name="velocity")
    tangent = _normalise_level(tangent_angle_level, name="tangent_angle")
    acceleration = _normalise_level(acceleration_level, name="acceleration")
    kinematic = max(velocity, tangent)
    candidate = max(interval, kinematic, acceleration)
    families = tuple(
        family
        for family, level in (
            ("interval", interval),
            ("kinematic_velocity_tangent", kinematic),
            ("acceleration", acceleration),
        )
        if level > WarningLevel.GREEN
    )

    raw_delta_state = (
        _normalise_delta_v_state(delta_v_state)
        if isinstance(delta_v_state, str)
        else None
    )
    acceleration_status = (
        "accelerating" if acceleration > WarningLevel.GREEN else "not_accelerating"
    )
    transition_status = _TRANSITION_STATUS_BY_DELTA_V.get(
        raw_delta_state, "delta_v_not_available"
    )
    if raw_delta_state is None:
        consistency = "delta_v_not_available"
    elif candidate > WarningLevel.GREEN:
        consistency = {
            "negative": "countertrend_to_elevated_candidate",
            "near_zero": "neutral_with_elevated_candidate",
            "positive": "corroborates_elevated_candidate",
        }[raw_delta_state]
    else:
        consistency = {
            "negative": "negative_trend_with_green_candidate",
            "near_zero": "consistent_green_and_near_steady_candidate",
            "positive": "positive_trend_without_elevated_candidate",
        }[raw_delta_state]

    if candidate == WarningLevel.GREEN:
        confirmation = f"all_evidence_green_{acceleration_status}"
    else:
        elevated = [
            family
            for family, level in (
                ("interval", interval),
                ("kinematic_velocity_tangent", kinematic),
                ("acceleration", acceleration),
            )
            if level == candidate
        ]
        confirmation = "candidate_supported_by_" + "_and_".join(elevated)

    composite_parts = [
        f"candidate_{candidate.color}",
        f"interval_{interval.color}",
        f"velocity_{velocity.color}",
        f"tangent_{tangent.color}",
        f"kinematic_{kinematic.color}",
        f"acceleration_{acceleration.color}",
    ]
    if raw_delta_state is not None:
        composite_parts.append(f"delta_v_{raw_delta_state}")
    composite_parts.append(consistency)
    reason = (
        f"{confirmation};acceleration={acceleration.color};"
        f"delta_v={raw_delta_state or 'not_available'};"
        f"transition={transition_status};consistency={consistency}"
    )
    return StationEvidenceResult(
        status="valid",
        candidate_level=candidate,
        kinematic_level=kinematic,
        evidence_families=families,
        acceleration_status=acceleration_status,
        trend_component=(
            None
            if raw_delta_state is None
            else _TREND_COMPONENT_BY_DELTA_V[raw_delta_state]
        ),
        transition_status=transition_status,
        evidence_consistency_status=consistency,
        composite_signal="__".join(composite_parts),
        confirmation_status=confirmation,
        reason=reason,
        input_statuses=tuple(statuses),
        acceleration_level=acceleration,
        acceleration_indicator_status="valid",
        acceleration_reason=f"configured_{acceleration.color}_range",
    )


__all__ = [
    "DELTA_V_STATES",
    "INPUT_STATUSES",
    "StationEvidenceResult",
    "fuse_station_evidence_families_v4",
]
