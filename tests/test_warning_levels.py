"""Regression tests for the shared five-level warning taxonomy."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.levels import WARNING_COLORS, WarningLevel  # noqa: E402
from warning.protocol import ProtocolValidationError, load_protocol  # noqa: E402


class WarningLevelTests(unittest.TestCase):
    def test_levels_expose_the_confirmed_five_color_order(self):
        self.assertEqual(
            tuple(level.value for level in WarningLevel),
            (0, 1, 2, 3, 4),
        )
        self.assertEqual(
            WARNING_COLORS,
            ("green", "blue", "yellow", "orange", "red"),
        )
        self.assertEqual(
            tuple(level.color for level in WarningLevel),
            WARNING_COLORS,
        )

    def test_protocol_rejects_a_warning_color_order_that_drifts_from_the_enum(self):
        protocol = load_protocol()
        protocol["confirmed"]["warning_levels"] = [
            "green",
            "yellow",
            "blue",
            "orange",
            "red",
        ]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "protocol.json"
            path.write_text(json.dumps(protocol), encoding="utf-8")

            with self.assertRaisesRegex(ProtocolValidationError, "warning_levels"):
                load_protocol(path)


if __name__ == "__main__":
    unittest.main()
