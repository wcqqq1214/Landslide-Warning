"""Non-formal v2 evidence-family and spatial-block fusion for Ootang.

The implementation is intentionally separate from the original two-support
draft rule.  It is an auditable operational alternative for the Ootang case:

* velocity and tangent angle are one kinematic evidence family, not two votes;
* interval deviation, kinematic intensity, and acceleration are reported as
  distinct dimensions;
* a high single-station candidate remains visible instead of becoming missing;
* whole-body confirmation requires spatial support across predeclared blocks.

It is not the multinomial-logistic fusion from the specified Word thesis and
does not create a formal warning result.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Integral
from typing import Any

from warning.levels import WARNING_LEVELS, WarningLevel
from warning.spatial_blocks import normalise_spatial_blocks

INPUT_STATUSES = ("valid", "warmup", "invalid", "not_applicable")
DELTA_V_STATES = ("negative", "near_zero", "positive")
_NONVALID_STATUS_PRECEDENCE = ("invalid", "not_applicable", "warmup")


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


def _normalise_input_statuses(
    values: Mapping[str, object],
    input_statuses: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...]:
    names = ("interval", "velocity", "tangent_angle", "delta_v")
    supplied = {} if input_statuses is None else dict(input_statuses)
    unknown = sorted(set(supplied).difference(names))
    if unknown:
        raise ValueError(f"input_statuses has unknown indicators: {unknown}")
    statuses: list[tuple[str, str]] = []
    for name in names:
        default = "valid" if values[name] is not None else "invalid"
        status = supplied.get(name, default)
        if status not in INPUT_STATUSES:
            raise ValueError(
                f"{name} status must be one of: {', '.join(INPUT_STATUSES)}"
            )
        statuses.append((name, status))
    return tuple(statuses)


@dataclass(frozen=True)
class StationEvidenceResult:
    """A visible, assessable station candidate and its evidence dimensions."""

    status: str
    candidate_level: WarningLevel | None
    kinematic_level: WarningLevel | None
    evidence_families: tuple[str, ...]
    acceleration_status: str | None
    confirmation_status: str
    reason: str
    input_statuses: tuple[tuple[str, str], ...]

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
        """Return backward-readable and v2-specific audit columns."""

        return {
            # ``fusion_status`` remains for operational-run manifest compatibility;
            # in v2 it means the station is assessable, not that two inputs voted.
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
            "station_confirmation_status": self.confirmation_status,
        }


def fuse_station_evidence_families(
    *,
    interval_level: int | WarningLevel | None,
    velocity_level: int | WarningLevel | None,
    tangent_angle_level: int | WarningLevel | None,
    delta_v_state: str | None,
    input_statuses: Mapping[str, str] | None = None,
) -> StationEvidenceResult:
    """Fuse interval and one kinematic family without double-counting.

    ``delta_v`` supplies an acceleration qualifier only.  It never changes the
    ordinal candidate color, which is the maximum of interval deviation and
    kinematic intensity.
    """

    values = {
        "interval": interval_level,
        "velocity": velocity_level,
        "tangent_angle": tangent_angle_level,
        "delta_v": delta_v_state,
    }
    statuses = _normalise_input_statuses(values, input_statuses)
    status_by_name = dict(statuses)
    for nonvalid in _NONVALID_STATUS_PRECEDENCE:
        if nonvalid in status_by_name.values():
            return StationEvidenceResult(
                status=nonvalid,
                candidate_level=None,
                kinematic_level=None,
                evidence_families=(),
                acceleration_status=None,
                confirmation_status=f"{nonvalid}_input",
                reason=f"{nonvalid}_input",
                input_statuses=statuses,
            )

    interval = _normalise_level(interval_level, name="interval")
    velocity = _normalise_level(velocity_level, name="velocity")
    tangent = _normalise_level(tangent_angle_level, name="tangent_angle")
    delta_state = _normalise_delta_v_state(delta_v_state)
    kinematic = max(velocity, tangent)
    candidate = max(interval, kinematic)
    families = tuple(
        family
        for family, level in (("interval", interval), ("kinematic", kinematic))
        if level > WarningLevel.GREEN
    )
    acceleration = "accelerating" if delta_state == "positive" else "not_accelerating"

    if candidate == WarningLevel.GREEN:
        confirmation = f"all_evidence_green_{acceleration}"
    elif interval > WarningLevel.GREEN and kinematic > WarningLevel.GREEN:
        confirmation = "interval_and_kinematic_elevated"
    elif interval > WarningLevel.GREEN:
        confirmation = f"interval_only_{acceleration}"
    else:
        confirmation = f"kinematic_only_{acceleration}"

    return StationEvidenceResult(
        status="valid",
        candidate_level=candidate,
        kinematic_level=kinematic,
        evidence_families=families,
        acceleration_status=acceleration,
        confirmation_status=confirmation,
        reason=confirmation,
        input_statuses=statuses,
    )


@dataclass(frozen=True)
class SpatialSiteFusionResult:
    """A site-level result that keeps coverage and confirmation separate."""

    status: str
    level: WarningLevel | None
    candidate_level: WarningLevel | None
    total_station_count: int
    assessable_station_count: int
    assessable_blocks: tuple[str, ...]
    coverage_complete: bool
    contributing_stations: tuple[str, ...]
    contributing_blocks: tuple[str, ...]
    candidate_stations: tuple[str, ...]
    candidate_blocks: tuple[str, ...]
    level_counts: tuple[tuple[WarningLevel, int], ...]
    reason: str

    @property
    def color(self) -> str | None:
        return None if self.level is None else self.level.color

    @property
    def candidate_color(self) -> str | None:
        return None if self.candidate_level is None else self.candidate_level.color

    def to_record(
        self,
        *,
        minimum_assessable_station_count: int,
        require_all_blocks_for_green: bool,
        minimum_supporting_stations: int,
        minimum_supporting_blocks: int,
        cross_block_confirmation_minimum_level: WarningLevel,
    ) -> dict[str, Any]:
        counts = dict(self.level_counts)
        return {
            "site_fusion_status": self.status,
            "site_level": None if self.level is None else int(self.level),
            "site_color": self.color,
            "site_candidate_level": (
                None if self.candidate_level is None else int(self.candidate_level)
            ),
            "site_candidate_color": self.candidate_color,
            "site_fusion_reason": self.reason,
            "total_station_count": self.total_station_count,
            "assessable_station_count": self.assessable_station_count,
            "minimum_assessable_station_count": minimum_assessable_station_count,
            "assessable_blocks": ";".join(self.assessable_blocks),
            "assessable_block_count": len(self.assessable_blocks),
            "require_all_blocks_for_green": require_all_blocks_for_green,
            "coverage_complete": self.coverage_complete,
            "minimum_supporting_stations": minimum_supporting_stations,
            "minimum_supporting_blocks": minimum_supporting_blocks,
            "cross_block_confirmation_minimum_level": int(
                cross_block_confirmation_minimum_level
            ),
            "cross_block_confirmation_minimum_color": (
                cross_block_confirmation_minimum_level.color
            ),
            "contributing_stations": ";".join(self.contributing_stations),
            "contributing_blocks": ";".join(self.contributing_blocks),
            "candidate_stations": ";".join(self.candidate_stations),
            "candidate_blocks": ";".join(self.candidate_blocks),
            **{
                f"station_count_{level.color}": counts[level]
                for level in WARNING_LEVELS
            },
        }


def fuse_site_spatial_blocks(
    station_results: Mapping[str, StationEvidenceResult],
    *,
    blocks: Mapping[str, tuple[str, ...] | list[str]],
    minimum_assessable_station_count: int,
    require_all_blocks_for_green: bool,
    minimum_supporting_stations: int,
    minimum_supporting_blocks: int,
    cross_block_confirmation_minimum_level: int
    | WarningLevel = WarningLevel.YELLOW,
) -> SpatialSiteFusionResult:
    """Apply the v2 coverage and cross-block site-confirmation rule.

    In the current profile, only yellow and higher candidates need cross-block
    confirmation.  A blue result is therefore visible only when blue is the
    highest candidate of the day.  An unconfirmed yellow--red candidate must
    not be silently downgraded to blue merely because independent blocks have
    blue candidates.
    """

    if not isinstance(station_results, Mapping):
        raise TypeError("station_results must be a mapping of station identifiers")
    if not station_results:
        raise ValueError("station_results must contain at least one station")
    block_records = normalise_spatial_blocks(blocks)
    block_names = tuple(name for name, _ in block_records)
    station_to_block = {
        station: name for name, stations in block_records for station in stations
    }
    unknown = sorted(set(station_results).difference(station_to_block))
    if unknown:
        raise ValueError("station_results has stations outside blocks: " + ", ".join(unknown))
    if isinstance(minimum_assessable_station_count, bool) or not isinstance(
        minimum_assessable_station_count, Integral
    ) or minimum_assessable_station_count < 1:
        raise ValueError("minimum_assessable_station_count must be a positive integer")
    for name, value in {
        "minimum_supporting_stations": minimum_supporting_stations,
        "minimum_supporting_blocks": minimum_supporting_blocks,
    }.items():
        if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    confirmation_minimum = _normalise_level(
        cross_block_confirmation_minimum_level,
        name="cross_block_confirmation_minimum",
    )
    if confirmation_minimum <= WarningLevel.GREEN:
        raise ValueError(
            "cross_block_confirmation_minimum must be blue, yellow, orange, or red"
        )

    records: list[tuple[str, StationEvidenceResult]] = []
    for station, result in station_results.items():
        if not isinstance(station, str) or not station.strip():
            raise ValueError("station identifiers must be non-blank strings")
        if not isinstance(result, StationEvidenceResult):
            raise TypeError("station results must be StationEvidenceResult instances")
        records.append((station, result))
    records.sort(key=lambda item: item[0])
    assessable = tuple(
        (station, result)
        for station, result in records
        if result.status == "valid" and result.candidate_level is not None
    )
    assessable_blocks = tuple(
        name
        for name in block_names
        if any(station_to_block[station] == name for station, _ in assessable)
    )
    coverage_complete = len(assessable) >= int(minimum_assessable_station_count) and (
        not require_all_blocks_for_green or assessable_blocks == block_names
    )
    level_counts = tuple(
        (
            level,
            sum(result.candidate_level == level for _, result in assessable),
        )
        for level in WARNING_LEVELS
    )

    candidate_level = (
        max(result.candidate_level for _, result in assessable)
        if assessable
        else None
    )
    candidate_stations = (
        tuple(
            station
            for station, result in assessable
            if result.candidate_level == candidate_level
        )
        if candidate_level is not None
        else ()
    )
    candidate_blocks = tuple(
        name
        for name in block_names
        if any(station_to_block[station] == name for station in candidate_stations)
    )

    # The minimum assessable-station gate is global.  It must be satisfied
    # before any colour (including blue--red) can be issued.  Spatial-block
    # completeness remains the v2 green-only requirement declared by the
    # ``require_all_blocks_for_green`` profile field.
    if len(assessable) < int(minimum_assessable_station_count):
        return SpatialSiteFusionResult(
            status="insufficient_assessable_coverage",
            level=None,
            candidate_level=candidate_level,
            total_station_count=len(records),
            assessable_station_count=len(assessable),
            assessable_blocks=assessable_blocks,
            coverage_complete=False,
            contributing_stations=(),
            contributing_blocks=(),
            candidate_stations=candidate_stations,
            candidate_blocks=candidate_blocks,
            level_counts=level_counts,
            reason="assessable_station_count_below_minimum",
        )

    confirmed_level: WarningLevel | None = None
    confirmed_stations: tuple[str, ...] = ()
    confirmed_blocks: tuple[str, ...] = ()
    for level in tuple(WARNING_LEVELS)[int(confirmation_minimum) :]:
        supporters = tuple(
            station
            for station, result in assessable
            if result.candidate_level is not None and result.candidate_level >= level
        )
        supporting_blocks = tuple(
            name
            for name in block_names
            if any(station_to_block[station] == name for station in supporters)
        )
        if (
            len(supporters) >= int(minimum_supporting_stations)
            and len(supporting_blocks) >= int(minimum_supporting_blocks)
        ):
            confirmed_level = level
            confirmed_stations = supporters
            confirmed_blocks = supporting_blocks

    if confirmed_level is not None:
        return SpatialSiteFusionResult(
            status="valid",
            level=confirmed_level,
            candidate_level=candidate_level,
            total_station_count=len(records),
            assessable_station_count=len(assessable),
            assessable_blocks=assessable_blocks,
            coverage_complete=coverage_complete,
            contributing_stations=confirmed_stations,
            contributing_blocks=confirmed_blocks,
            candidate_stations=candidate_stations,
            candidate_blocks=candidate_blocks,
            level_counts=level_counts,
            reason=(
                f"{minimum_supporting_stations}_station_"
                f"{minimum_supporting_blocks}_block_candidate_"
                f"{confirmed_level.color}"
            ),
        )
    if (
        candidate_level is not None
        and candidate_level >= confirmation_minimum
    ):
        return SpatialSiteFusionResult(
            status="candidate_not_site_confirmed",
            level=None,
            candidate_level=candidate_level,
            total_station_count=len(records),
            assessable_station_count=len(assessable),
            assessable_blocks=assessable_blocks,
            coverage_complete=coverage_complete,
            contributing_stations=(),
            contributing_blocks=(),
            candidate_stations=candidate_stations,
            candidate_blocks=candidate_blocks,
            level_counts=level_counts,
            reason=(
                f"no_{minimum_supporting_stations}_station_"
                f"{minimum_supporting_blocks}_block_"
                f"{confirmation_minimum.color}_or_higher_support"
            ),
        )
    if candidate_level == WarningLevel.BLUE:
        blue_stations = tuple(
            station
            for station, result in assessable
            if result.candidate_level is not None
            and result.candidate_level >= WarningLevel.BLUE
        )
        blue_blocks = tuple(
            name
            for name in block_names
            if any(station_to_block[station] == name for station in blue_stations)
        )
        return SpatialSiteFusionResult(
            status="valid",
            level=WarningLevel.BLUE,
            candidate_level=WarningLevel.BLUE,
            total_station_count=len(records),
            assessable_station_count=len(assessable),
            assessable_blocks=assessable_blocks,
            coverage_complete=coverage_complete,
            contributing_stations=blue_stations,
            contributing_blocks=blue_blocks,
            candidate_stations=candidate_stations,
            candidate_blocks=candidate_blocks,
            level_counts=level_counts,
            reason="highest_candidate_blue_no_cross_block_confirmation_required",
        )
    if coverage_complete:
        return SpatialSiteFusionResult(
            status="valid",
            level=WarningLevel.GREEN,
            candidate_level=WarningLevel.GREEN,
            total_station_count=len(records),
            assessable_station_count=len(assessable),
            assessable_blocks=assessable_blocks,
            coverage_complete=True,
            contributing_stations=(),
            contributing_blocks=(),
            candidate_stations=(),
            candidate_blocks=(),
            level_counts=level_counts,
            reason="all_assessable_station_candidates_green_with_complete_coverage",
        )
    return SpatialSiteFusionResult(
        status="insufficient_assessable_coverage",
        level=None,
        candidate_level=candidate_level,
        total_station_count=len(records),
        assessable_station_count=len(assessable),
        assessable_blocks=assessable_blocks,
        coverage_complete=False,
        contributing_stations=(),
        contributing_blocks=(),
        candidate_stations=candidate_stations,
        candidate_blocks=candidate_blocks,
        level_counts=level_counts,
        reason="assessable_station_or_spatial_block_coverage_incomplete",
    )


__all__ = [
    "DELTA_V_STATES",
    "INPUT_STATUSES",
    "SpatialSiteFusionResult",
    "StationEvidenceResult",
    "fuse_site_spatial_blocks",
    "fuse_station_evidence_families",
]
