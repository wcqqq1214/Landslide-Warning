"""Public gate tests for the future formal-warning entry point."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.formal_warning import (  # noqa: E402
    FormalWarningExecutorUnavailableError,
    run_formal_warning,
)
from warning.protocol import ProtocolNotFrozenError, load_protocol  # noqa: E402


class FormalWarningEntryTests(unittest.TestCase):
    def test_draft_protocol_blocks_the_formal_entry(self):
        with self.assertRaises(ProtocolNotFrozenError):
            run_formal_warning()

    def test_frozen_protocol_cannot_run_until_a_formal_executor_exists(self):
        frozen = copy.deepcopy(load_protocol())
        frozen["status"] = "frozen"
        frozen["unresolved_items"] = []

        with tempfile.TemporaryDirectory() as directory:
            protocol_path = Path(directory) / "frozen.json"
            protocol_path.write_text(
                json.dumps(frozen, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaises(FormalWarningExecutorUnavailableError):
                run_formal_warning(protocol_path=protocol_path)


if __name__ == "__main__":
    unittest.main()
