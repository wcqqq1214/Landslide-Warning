"""Diagnostic-only multi-station summary for the draft warning inputs.

This module deliberately does not define the required landslide-body function
``F_site``.  It reports the distribution of independently computed station
results so a later, frozen site rule can be audited without treating a maximum
station level as an integrated warning level or color.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from warning.levels import WARNING_LEVELS, WarningLevel
from warning.rule_fusion import StationFusionResult


SITE_FUSION_AUDIT_STATUS = "diagnostic_only_unconfigured_site_rule"
SITE_FUSION_AUDIT_REASON = "site_rule_unconfigured"
STATION_RESULT_STATUSES = (
    "valid",
    "warmup",
    "invalid",
    "not_applicable",
    "uncorroborated",
)


@dataclass(frozen=True)
class SiteFusionAudit:
    """Auditable input summary that intentionally has no integrated level."""

    status: str
    total_station_count: int
    valid_station_count: int
    elevated_station_count: int
    max_station_level: WarningLevel | None
    max_level_stations: tuple[str, ...]
    level_counts: tuple[tuple[WarningLevel, int], ...]
    station_status_counts: tuple[tuple[str, int], ...]
    uncorroborated_stations: tuple[str, ...]
    reason: str

    def to_record(self) -> dict[str, Any]:
        """Return scalar audit fields without inventing an ``F_site`` output."""

        level_counts = dict(self.level_counts)
        record: dict[str, Any] = {
            "site_fusion_status": self.status,
            "formal_warning_output": False,
            "integrated_level": None,
            "integrated_color": None,
            "total_station_count": self.total_station_count,
            "valid_station_count": self.valid_station_count,
            "elevated_station_count": self.elevated_station_count,
            "max_station_level": (
                None
                if self.max_station_level is None
                else int(self.max_station_level)
            ),
            "max_station_color": (
                None
                if self.max_station_level is None
                else self.max_station_level.color
            ),
            "max_level_stations": ";".join(self.max_level_stations),
            "uncorroborated_stations": ";".join(self.uncorroborated_stations),
            "site_fusion_reason": self.reason,
        }
        record.update(
            {
                f"station_count_{level.color}": level_counts[level]
                for level in WARNING_LEVELS
            }
        )
        record.update(
            {
                f"station_result_status_{status}": count
                for status, count in self.station_status_counts
            }
        )
        return record


def _validate_station_results(
    station_results: Mapping[str, StationFusionResult],
) -> tuple[tuple[str, StationFusionResult], ...]:
    if not isinstance(station_results, Mapping):
        raise TypeError("station_results must be a mapping of station identifiers")
    if not station_results:
        raise ValueError("station_results must contain at least one station")

    validated: list[tuple[str, StationFusionResult]] = []
    for station, result in station_results.items():
        if not isinstance(station, str) or not station.strip():
            raise ValueError("station identifiers must be non-blank strings")
        if not isinstance(result, StationFusionResult):
            raise TypeError("station results must be StationFusionResult instances")
        if result.status not in STATION_RESULT_STATUSES:
            raise ValueError(f"station {station!r} has an unknown result status")
        if result.status == "valid" and not isinstance(result.level, WarningLevel):
            raise ValueError(f"valid station {station!r} must have a WarningLevel")
        if result.status != "valid" and result.level is not None:
            raise ValueError(f"non-valid station {station!r} must not have a warning level")
        validated.append((station, result))
    return tuple(sorted(validated, key=lambda item: item[0]))


def summarize_site_fusion_inputs(
    station_results: Mapping[str, StationFusionResult],
) -> SiteFusionAudit:
    """Summarize station results while preserving the unresolved ``F_site`` gate.

    A station contributes to level counts only when its per-station draft result
    is explicitly ``valid`` and carries a level.  In particular, an
    ``uncorroborated`` station with a high candidate input remains separate from
    both green and elevated station counts.
    """

    records = _validate_station_results(station_results)
    valid_records = tuple(
        (station, result)
        for station, result in records
        if result.status == "valid" and result.level is not None
    )
    level_counts = tuple(
        (
            level,
            sum(result.level == level for _, result in valid_records),
        )
        for level in WARNING_LEVELS
    )
    status_counts = tuple(
        (
            status,
            sum(result.status == status for _, result in records),
        )
        for status in STATION_RESULT_STATUSES
    )
    max_station_level = (
        max(result.level for _, result in valid_records)
        if valid_records
        else None
    )
    max_level_stations = (
        tuple(
            station
            for station, result in valid_records
            if result.level == max_station_level
        )
        if max_station_level is not None
        else ()
    )

    return SiteFusionAudit(
        status=SITE_FUSION_AUDIT_STATUS,
        total_station_count=len(records),
        valid_station_count=len(valid_records),
        elevated_station_count=sum(
            result.level > WarningLevel.GREEN for _, result in valid_records
        ),
        max_station_level=max_station_level,
        max_level_stations=max_level_stations,
        level_counts=level_counts,
        station_status_counts=status_counts,
        uncorroborated_stations=tuple(
            station
            for station, result in records
            if result.status == "uncorroborated"
        ),
        reason=SITE_FUSION_AUDIT_REASON,
    )


__all__ = [
    "SITE_FUSION_AUDIT_REASON",
    "SITE_FUSION_AUDIT_STATUS",
    "STATION_RESULT_STATUSES",
    "SiteFusionAudit",
    "summarize_site_fusion_inputs",
]
