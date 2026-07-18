"""Write calibration-only interval diagnostics without a warning decision.

This runner materializes raw, per-station evidence from the existing ConvLSTM
prediction table.  It is deliberately separate from a formal warning runner:
it never evaluates a calibration gate, produces no interval color/level, and
never allows test rows into the diagnostic frame.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import sys

import pandas as pd

# Permit ``uv run python code/warning/interval_diagnostics.py`` as well as
# package imports from the test suite.
CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.interval_state import (  # noqa: E402
    CALIBRATION_SPLIT,
    REQUIRED_INTERVAL_COLUMNS,
    summarize_calibration_interval_inputs,
)
from warning.protocol import (  # noqa: E402
    DEFAULT_PROTOCOL_PATH,
    load_protocol,
    protocol_content_sha256,
    unresolved_item_ids,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREDICTIONS_PATH = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "warning_draft"
SUMMARY_FILENAME = "interval_calibration_diagnostics.csv"
MANIFEST_FILENAME = "interval_calibration_diagnostics_manifest.json"
DIAGNOSTIC_STATUS = "diagnostic_only_no_gate_decision"
_PREDICTION_COLUMNS = ("split", "station", *REQUIRED_INTERVAL_COLUMNS)


@dataclass(frozen=True)
class CalibrationDiagnosticArtifacts:
    """Paths and input count for one non-formal calibration diagnostic run."""

    summary_path: Path
    manifest_path: Path
    n_calibration_rows: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_calibration_input(calibration: pd.DataFrame) -> str:
    """Hash only the selected calibration inputs in a canonical CSV form.

    A whole-prediction-file hash would change when a held-out test row changes.
    That is useful provenance in some contexts, but it would make this strictly
    calibration-only artifact look test-sensitive.  The canonical slice records
    precisely the rows and columns used for the raw diagnostic summary instead.
    """

    canonical = calibration.loc[:, _PREDICTION_COLUMNS]
    buffer = io.StringIO()
    canonical.to_csv(
        buffer,
        index=False,
        lineterminator="\n",
        na_rep="<NA>",
        float_format="%.17g",
    )
    return hashlib.sha256(buffer.getvalue().encode("utf-8")).hexdigest()


def load_calibration_predictions(predictions_path: str | Path) -> pd.DataFrame:
    """Load only ``split=calibration`` rows needed for interval diagnostics."""

    path = Path(predictions_path)
    try:
        chunks = pd.read_csv(path, usecols=list(_PREDICTION_COLUMNS), chunksize=10_000)
    except FileNotFoundError as exc:
        raise ValueError(f"prediction file does not exist: {path}") from exc
    except ValueError as exc:
        required = ", ".join(_PREDICTION_COLUMNS)
        raise ValueError(
            f"prediction file must include interval diagnostic columns: {required}"
        ) from exc

    calibration_chunks: list[pd.DataFrame] = []
    for chunk in chunks:
        selected = chunk.loc[
            chunk["split"].astype("string").eq(CALIBRATION_SPLIT)
        ].copy()
        if not selected.empty:
            calibration_chunks.append(selected)
    if not calibration_chunks:
        raise ValueError("prediction file contains no split=calibration rows")

    calibration = pd.concat(calibration_chunks, ignore_index=True)
    if calibration["station"].isna().any():
        raise ValueError("calibration diagnostics require a nonmissing station")
    return calibration


def build_calibration_diagnostics(predictions_path: str | Path) -> pd.DataFrame:
    """Return raw calibration evidence without metadata or pass/fail fields."""

    calibration = load_calibration_predictions(predictions_path)
    return summarize_calibration_interval_inputs(calibration)


def write_calibration_diagnostics(
    *,
    predictions_path: str | Path = DEFAULT_PREDICTIONS_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> CalibrationDiagnosticArtifacts:
    """Write a versioned calibration diagnostic CSV and non-formal manifest."""

    source_path = Path(predictions_path)
    protocol = load_protocol(protocol_path)
    protocol_sha256 = protocol_content_sha256(protocol)
    calibration = load_calibration_predictions(source_path)
    summary = summarize_calibration_interval_inputs(calibration)
    calibration_input_sha256 = _sha256_calibration_input(calibration)

    summary = summary.copy()
    metadata = {
        "protocol_id": protocol["protocol_id"],
        "protocol_version": protocol["protocol_version"],
        "protocol_status": protocol["status"],
        "protocol_content_sha256": protocol_sha256,
        "diagnostic_status": DIAGNOSTIC_STATUS,
        "source_split": CALIBRATION_SPLIT,
        "calibration_input_sha256": calibration_input_sha256,
    }
    for column, value in reversed(tuple(metadata.items())):
        summary.insert(0, column, value)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / SUMMARY_FILENAME
    manifest_path = out_dir / MANIFEST_FILENAME
    summary.to_csv(summary_path, index=False)

    manifest = {
        "artifact_kind": "ootang_interval_calibration_diagnostics",
        "diagnostic_status": DIAGNOSTIC_STATUS,
        "formal_warning_output": False,
        "protocol": {
            "id": protocol["protocol_id"],
            "version": protocol["protocol_version"],
            "status": protocol["status"],
            "content_sha256": protocol_sha256,
            "unresolved_item_ids": list(unresolved_item_ids(protocol)),
        },
        "selection": {
            "split": CALIBRATION_SPLIT,
            "n_rows": int(len(calibration)),
            "stations": sorted(calibration["station"].astype(str).unique().tolist()),
        },
        "source_predictions": {
            "path": str(source_path),
            "selected_columns": list(_PREDICTION_COLUMNS),
            "selected_split": CALIBRATION_SPLIT,
        },
        "calibration_input": {
            "sha256": calibration_input_sha256,
            "canonicalization": "csv_utf8_lf_na_<NA>_float_%.17g",
        },
        "summary": {
            "path": str(summary_path),
            "sha256": _sha256_file(summary_path),
            "columns": list(summary.columns),
        },
        "not_evaluated": [
            "interval_calibration_gate",
            "five_level_interval_mapping",
            "test_split_threshold_selection",
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return CalibrationDiagnosticArtifacts(
        summary_path=summary_path,
        manifest_path=manifest_path,
        n_calibration_rows=len(calibration),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write calibration-only interval diagnostics without warning levels."
    )
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    artifacts = write_calibration_diagnostics(
        predictions_path=args.predictions,
        output_dir=args.output_dir,
        protocol_path=args.protocol,
    )
    print(
        "wrote calibration-only interval diagnostics "
        f"({artifacts.n_calibration_rows} rows): {artifacts.summary_path}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "CalibrationDiagnosticArtifacts",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PREDICTIONS_PATH",
    "DIAGNOSTIC_STATUS",
    "build_calibration_diagnostics",
    "load_calibration_predictions",
    "write_calibration_diagnostics",
]
