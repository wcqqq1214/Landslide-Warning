"""Run the separately versioned Ootang v3 dual-axis operational draft."""

from __future__ import annotations

import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.operational_run import write_ootang_operational_run
from warning.operational_v3_full_timeline import (
    write_ootang_v3_full_timeline_figure,
)
from warning.operational_v3_station_diagnostic import (
    DEFAULT_FIGURE_SPEC_PATH as DEFAULT_STATION_DIAGNOSTIC_SPEC_PATH,
)
from warning.operational_v3_station_diagnostic import (
    write_ootang_v3_station_combined_diagnostic,
)
from warning.operational_v3_typical_days import (
    DEFAULT_FIGURE_SPEC_PATH,
    write_ootang_v3_typical_day_figure,
)

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "config" / "ootang_operational_run.v3.draft.json"
OUTPUT_DIR = ROOT / "figures" / "warning_operational_draft_v3"


def main() -> None:
    artifacts = write_ootang_operational_run(
        profile_path=PROFILE_PATH,
        output_dir=OUTPUT_DIR,
    )
    typical_day_artifacts = write_ootang_v3_typical_day_figure(
        station_timeline_path=artifacts.station_timeline_path,
        site_timeline_path=artifacts.site_timeline_path,
        run_manifest_path=artifacts.manifest_path,
        profile_path=PROFILE_PATH,
        figure_spec_path=DEFAULT_FIGURE_SPEC_PATH,
        output_dir=OUTPUT_DIR,
    )
    full_timeline_artifacts = write_ootang_v3_full_timeline_figure(
        station_timeline_path=artifacts.station_timeline_path,
        site_timeline_path=artifacts.site_timeline_path,
        run_manifest_path=artifacts.manifest_path,
        profile_path=PROFILE_PATH,
        figure_spec_path=DEFAULT_FIGURE_SPEC_PATH,
        output_dir=OUTPUT_DIR,
    )
    station_diagnostic_artifacts = write_ootang_v3_station_combined_diagnostic(
        station_timeline_path=artifacts.station_timeline_path,
        site_timeline_path=artifacts.site_timeline_path,
        run_manifest_path=artifacts.manifest_path,
        profile_path=PROFILE_PATH,
        figure_spec_path=DEFAULT_STATION_DIAGNOSTIC_SPEC_PATH,
        output_dir=OUTPUT_DIR,
    )
    print(
        "wrote non-formal Ootang v3 dual-axis operational timeline: "
        f"{artifacts.manifest_path}; representative-day audit: "
        f"{typical_day_artifacts.manifest_path}; complete 514-day "
        "observed-after-forecast audit: "
        f"{full_timeline_artifacts.manifest_path}; all-station combined "
        f"diagnostic: {station_diagnostic_artifacts.manifest_path}"
    )


if __name__ == "__main__":
    main()
