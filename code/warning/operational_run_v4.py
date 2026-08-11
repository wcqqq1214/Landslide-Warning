"""Run the independently versioned Ootang v4 acceleration draft."""

from __future__ import annotations

import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.operational_run import write_ootang_operational_run
from warning.operational_v4_figures import (
    write_v4_full_timeline,
    write_v4_station_combined_diagnostic,
    write_v4_typical_days,
)

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "config" / "ootang_operational_run.v4.draft.json"
OUTPUT_DIR = ROOT / "figures" / "warning_operational_draft_v4"


def main() -> None:
    artifacts = write_ootang_operational_run(
        profile_path=PROFILE_PATH,
        output_dir=OUTPUT_DIR,
        evidence_dir=ROOT / "figures" / "warning_draft_v4",
    )
    station_figure = write_v4_station_combined_diagnostic(
        station_path=artifacts.station_timeline_path,
        site_path=artifacts.site_timeline_path,
        core_manifest_path=artifacts.manifest_path,
        profile_path=PROFILE_PATH,
        output_dir=OUTPUT_DIR,
    )
    timeline_figure = write_v4_full_timeline(
        station_path=artifacts.station_timeline_path,
        site_path=artifacts.site_timeline_path,
        core_manifest_path=artifacts.manifest_path,
        profile_path=PROFILE_PATH,
        output_dir=OUTPUT_DIR,
    )
    typical_days = write_v4_typical_days(
        station_path=artifacts.station_timeline_path,
        site_path=artifacts.site_timeline_path,
        core_manifest_path=artifacts.manifest_path,
        profile_path=PROFILE_PATH,
        output_dir=OUTPUT_DIR,
    )
    print(
        "wrote non-formal Ootang v4 acceleration draft: "
        f"{artifacts.manifest_path}; figures: {station_figure}, "
        f"{timeline_figure}, {typical_days}"
    )


if __name__ == "__main__":
    main()
