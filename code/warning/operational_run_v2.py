"""Run the separately versioned Ootang v2 spatial operational draft."""

from __future__ import annotations

import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.operational_run import write_ootang_operational_run

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "config" / "ootang_operational_run.v2.draft.json"
OUTPUT_DIR = ROOT / "figures" / "warning_operational_draft_v2"


def main() -> None:
    artifacts = write_ootang_operational_run(
        profile_path=PROFILE_PATH,
        output_dir=OUTPUT_DIR,
    )
    print(
        "wrote non-formal Ootang v2 spatial operational timeline: "
        f"{artifacts.manifest_path}"
    )


if __name__ == "__main__":
    main()
