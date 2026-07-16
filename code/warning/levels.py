"""Shared, ordered warning-level vocabulary.

This module fixes only the mentor-confirmed five-color order.  It deliberately
does not encode a threshold, tolerance, or fusion rule.
"""

from __future__ import annotations

from enum import IntEnum


class WarningLevel(IntEnum):
    """Increasing warning severity in the approved five-level color system."""

    GREEN = 0
    BLUE = 1
    YELLOW = 2
    ORANGE = 3
    RED = 4

    @property
    def color(self) -> str:
        """Return the lowercase color token used in configs and outputs."""

        return self.name.lower()


WARNING_LEVELS: tuple[WarningLevel, ...] = tuple(WarningLevel)
WARNING_COLORS: tuple[str, ...] = tuple(level.color for level in WARNING_LEVELS)


__all__ = ["WARNING_COLORS", "WARNING_LEVELS", "WarningLevel"]
