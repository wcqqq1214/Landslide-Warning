"""Non-formal v3 whole-body/local-candidate fusion for the Ootang case.

The v3 draft keeps two questions separate:

* ``site_confirmed_level``: is there spatially corroborated whole-body evidence?
* ``local_max_candidate_level``: what is the highest assessable local candidate?

No output from this module is a formal warning result.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Integral
from typing import Any

from warning.levels import WARNING_LEVELS, WarningLevel
from warning.operational_v2_fusion import StationEvidenceResult
from warning.spatial_blocks import normalise_spatial_blocks


@dataclass(frozen=True)
class SpatialSiteFusionV3Result:
    """Site corroboration and local-candidate state reported on separate axes."""

    status: str
    site_confirmed_level: WarningLevel | None
    local_max_candidate_level: WarningLevel | None
    total_station_count: int
    assessable_station_count: int
    assessable_blocks: tuple[str, ...]
    coverage_complete: bool
    contributing_stations: tuple[str, ...]
    contributing_blocks: tuple[str, ...]
    local_max_candidate_stations: tuple[str, ...]
    local_max_candidate_blocks: tuple[str, ...]
    level_counts: tuple[tuple[WarningLevel, int], ...]
    local_attention_status: str
    reason: str

    @property
    def site_confirmed_color(self) -> str | None:
        return (
            None
            if self.site_confirmed_level is None
            else self.site_confirmed_level.color
        )

    @property
    def local_max_candidate_color(self) -> str | None:
        return (
            None
            if self.local_max_candidate_level is None
            else self.local_max_candidate_level.color
        )

    def to_record(
        self,
        *,
        minimum_assessable_station_count: int,
        require_all_blocks_for_any_site_level: bool,
        minimum_supporting_stations: int,
        minimum_supporting_blocks: int,
        higher_confirmation_minimum_level: WarningLevel = WarningLevel.YELLOW,
    ) -> dict[str, Any]:
        """Return explicit v3 axes plus stable v2-compatible aliases."""

        counts = dict(self.level_counts)
        site_level = (
            None if self.site_confirmed_level is None else int(self.site_confirmed_level)
        )
        local_level = (
            None
            if self.local_max_candidate_level is None
            else int(self.local_max_candidate_level)
        )
        return {
            "site_fusion_status": self.status,
            "site_level": site_level,
            "site_color": self.site_confirmed_color,
            "site_candidate_level": local_level,
            "site_candidate_color": self.local_max_candidate_color,
            "site_fusion_reason": self.reason,
            "site_confirmed_level": site_level,
            "site_confirmed_color": self.site_confirmed_color,
            "local_max_candidate_level": local_level,
            "local_max_candidate_color": self.local_max_candidate_color,
            "local_attention_status": self.local_attention_status,
            "total_station_count": self.total_station_count,
            "assessable_station_count": self.assessable_station_count,
            "minimum_assessable_station_count": minimum_assessable_station_count,
            "assessable_blocks": ";".join(self.assessable_blocks),
            "assessable_block_count": len(self.assessable_blocks),
            "require_all_blocks_for_any_site_level": (
                require_all_blocks_for_any_site_level
            ),
            "coverage_complete": self.coverage_complete,
            "minimum_supporting_stations": minimum_supporting_stations,
            "minimum_supporting_blocks": minimum_supporting_blocks,
            "cross_block_confirmation_minimum_level": int(WarningLevel.BLUE),
            "cross_block_confirmation_minimum_color": WarningLevel.BLUE.color,
            "higher_confirmation_minimum_level": int(
                higher_confirmation_minimum_level
            ),
            "higher_confirmation_minimum_color": (
                higher_confirmation_minimum_level.color
            ),
            "contributing_stations": ";".join(self.contributing_stations),
            "contributing_blocks": ";".join(self.contributing_blocks),
            "candidate_stations": ";".join(self.local_max_candidate_stations),
            "candidate_blocks": ";".join(self.local_max_candidate_blocks),
            "local_max_candidate_stations": ";".join(
                self.local_max_candidate_stations
            ),
            "local_max_candidate_blocks": ";".join(
                self.local_max_candidate_blocks
            ),
            **{
                f"station_count_{level.color}": counts[level]
                for level in WARNING_LEVELS
            },
        }


def fuse_site_spatial_blocks_v3(
    station_results: Mapping[str, StationEvidenceResult],
    *,
    blocks: Mapping[str, tuple[str, ...] | list[str]],
    minimum_assessable_station_count: int,
    require_all_blocks_for_any_site_level: bool,
    minimum_supporting_stations: int,
    minimum_supporting_blocks: int,
    higher_confirmation_minimum_level: int
    | WarningLevel = WarningLevel.YELLOW,
) -> SpatialSiteFusionV3Result:
    """Apply the v3 coverage gate before issuing any whole-body site colour."""

    if not isinstance(station_results, Mapping):
        raise TypeError("station_results must be a mapping of station identifiers")
    if not station_results:
        raise ValueError("station_results must contain at least one station")
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
    if isinstance(higher_confirmation_minimum_level, bool) or not isinstance(
        higher_confirmation_minimum_level, Integral
    ):
        raise TypeError("higher_confirmation_minimum_level must be a warning level")
    try:
        higher_confirmation_minimum = WarningLevel(
            int(higher_confirmation_minimum_level)
        )
    except ValueError as exc:
        raise ValueError(
            "higher_confirmation_minimum_level must be yellow, orange, or red"
        ) from exc
    if higher_confirmation_minimum < WarningLevel.YELLOW:
        raise ValueError(
            "higher_confirmation_minimum_level must be yellow, orange, or red"
        )

    block_records = normalise_spatial_blocks(blocks)
    block_names = tuple(name for name, _ in block_records)
    station_to_block = {
        station: name for name, stations in block_records for station in stations
    }
    unknown = sorted(set(station_results).difference(station_to_block))
    if unknown:
        raise ValueError(
            "station_results has stations outside blocks: " + ", ".join(unknown)
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
        not require_all_blocks_for_any_site_level or assessable_blocks == block_names
    )
    level_counts = tuple(
        (
            level,
            sum(result.candidate_level == level for _, result in assessable),
        )
        for level in WARNING_LEVELS
    )
    local_max = (
        max(result.candidate_level for _, result in assessable) if assessable else None
    )
    local_stations = (
        tuple(
            station
            for station, result in assessable
            if result.candidate_level == local_max
        )
        if local_max is not None
        else ()
    )
    local_blocks = tuple(
        name
        for name in block_names
        if any(station_to_block[station] == name for station in local_stations)
    )

    if not coverage_complete:
        return SpatialSiteFusionV3Result(
            status="insufficient_assessable_coverage",
            site_confirmed_level=None,
            local_max_candidate_level=local_max,
            total_station_count=len(records),
            assessable_station_count=len(assessable),
            assessable_blocks=assessable_blocks,
            coverage_complete=False,
            contributing_stations=(),
            contributing_blocks=(),
            local_max_candidate_stations=local_stations,
            local_max_candidate_blocks=local_blocks,
            level_counts=level_counts,
            local_attention_status="not_assessed",
            reason="assessable_station_or_spatial_block_coverage_incomplete",
        )

    if local_max == WarningLevel.GREEN:
        return SpatialSiteFusionV3Result(
            status="valid",
            site_confirmed_level=WarningLevel.GREEN,
            local_max_candidate_level=WarningLevel.GREEN,
            total_station_count=len(records),
            assessable_station_count=len(assessable),
            assessable_blocks=assessable_blocks,
            coverage_complete=True,
            contributing_stations=(),
            contributing_blocks=(),
            local_max_candidate_stations=local_stations,
            local_max_candidate_blocks=local_blocks,
            level_counts=level_counts,
            local_attention_status="none",
            reason="all_assessable_station_candidates_green_with_complete_coverage",
        )

    if local_max == WarningLevel.BLUE:
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
        if not (
            len(blue_stations) >= int(minimum_supporting_stations)
            and len(blue_blocks) >= int(minimum_supporting_blocks)
        ):
            return SpatialSiteFusionV3Result(
                status="valid",
                site_confirmed_level=WarningLevel.GREEN,
                local_max_candidate_level=WarningLevel.BLUE,
                total_station_count=len(records),
                assessable_station_count=len(assessable),
                assessable_blocks=assessable_blocks,
                coverage_complete=True,
                contributing_stations=(),
                contributing_blocks=(),
                local_max_candidate_stations=local_stations,
                local_max_candidate_blocks=local_blocks,
                level_counts=level_counts,
                local_attention_status="localized_blue_attention",
                reason="blue_candidate_lacks_cross_block_site_support",
            )
        return SpatialSiteFusionV3Result(
            status="valid",
            site_confirmed_level=WarningLevel.BLUE,
            local_max_candidate_level=WarningLevel.BLUE,
            total_station_count=len(records),
            assessable_station_count=len(assessable),
            assessable_blocks=assessable_blocks,
            coverage_complete=True,
            contributing_stations=blue_stations,
            contributing_blocks=blue_blocks,
            local_max_candidate_stations=local_stations,
            local_max_candidate_blocks=local_blocks,
            level_counts=level_counts,
            local_attention_status="none",
            reason=(
                f"{minimum_supporting_stations}_station_"
                f"{minimum_supporting_blocks}_block_candidate_blue"
            ),
        )

    confirmed_level: WarningLevel | None = None
    confirmed_stations: tuple[str, ...] = ()
    confirmed_blocks: tuple[str, ...] = ()
    for level in tuple(WARNING_LEVELS)[int(higher_confirmation_minimum) :]:
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

    if confirmed_level is None:
        return SpatialSiteFusionV3Result(
            status="candidate_not_site_confirmed",
            site_confirmed_level=None,
            local_max_candidate_level=local_max,
            total_station_count=len(records),
            assessable_station_count=len(assessable),
            assessable_blocks=assessable_blocks,
            coverage_complete=True,
            contributing_stations=(),
            contributing_blocks=(),
            local_max_candidate_stations=local_stations,
            local_max_candidate_blocks=local_blocks,
            level_counts=level_counts,
            local_attention_status="none",
            reason=(
                f"no_{minimum_supporting_stations}_station_"
                f"{minimum_supporting_blocks}_block_"
                f"{higher_confirmation_minimum.color}_or_higher_support"
            ),
        )
    return SpatialSiteFusionV3Result(
        status="valid",
        site_confirmed_level=confirmed_level,
        local_max_candidate_level=local_max,
        total_station_count=len(records),
        assessable_station_count=len(assessable),
        assessable_blocks=assessable_blocks,
        coverage_complete=True,
        contributing_stations=confirmed_stations,
        contributing_blocks=confirmed_blocks,
        local_max_candidate_stations=local_stations,
        local_max_candidate_blocks=local_blocks,
        level_counts=level_counts,
        local_attention_status="none",
        reason=(
            f"{minimum_supporting_stations}_station_"
            f"{minimum_supporting_blocks}_block_candidate_{confirmed_level.color}"
        ),
    )


__all__ = ["SpatialSiteFusionV3Result", "fuse_site_spatial_blocks_v3"]
