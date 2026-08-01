"""Shared spatial-block normalization for operational site fusion."""

from __future__ import annotations

from collections.abc import Mapping


def normalise_spatial_blocks(
    blocks: Mapping[str, tuple[str, ...] | list[str]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Validate spatial-block membership and return normalized records."""

    if not isinstance(blocks, Mapping) or not blocks:
        raise ValueError("blocks must be a non-empty mapping")
    records: list[tuple[str, tuple[str, ...]]] = []
    seen_stations: set[str] = set()
    for name, stations in blocks.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("block identifiers must be non-blank strings")
        if not isinstance(stations, (tuple, list)) or not stations:
            raise ValueError(f"block {name!r} must contain at least one station")
        normalized = tuple(str(station).strip() for station in stations)
        if any(not station for station in normalized):
            raise ValueError(f"block {name!r} contains a blank station")
        duplicate = sorted(set(normalized).intersection(seen_stations))
        if duplicate:
            raise ValueError(
                "each station must belong to exactly one spatial block; duplicate: "
                + ", ".join(duplicate)
            )
        seen_stations.update(normalized)
        records.append((name, normalized))
    return tuple(records)


__all__ = ["normalise_spatial_blocks"]
