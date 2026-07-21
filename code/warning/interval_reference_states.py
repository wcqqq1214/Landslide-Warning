"""Write source-referenced observed interval states without a formal warning.

The specified thesis Figure 5-1 supplies the relative five-level regions.
This runner applies those regions only where the prediction was issued before
the observed target: the calibration and test rows.  Fit rows remain explicit
diagnostics and are not silently relabeled as green.
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


# Permit direct execution with ``uv run python code/warning/...py`` as well as
# package imports from the test suite.
CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.interval_state import (  # noqa: E402
    REQUIRED_INTERVAL_COLUMNS,
    THESIS_FIGURE_5_1_MAPPING_ID,
    classify_observed_interval_states,
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
STATES_FILENAME = "interval_reference_states.csv"
MANIFEST_FILENAME = "interval_reference_states_manifest.json"
STATE_ARTIFACT_STATUS = "source_referenced_observed_interval_state_not_formal"
FIT_SPLIT = "fit"
ISSUED_SPLITS = ("calibration", "test")
PREDICTION_SPLITS = (FIT_SPLIT, *ISSUED_SPLITS)
ARTIFACT_INPUT_STATUS_COLUMN = "interval_input_status"
_PREDICTION_COLUMNS = ("date", "station", "split", *REQUIRED_INTERVAL_COLUMNS)
_NATURAL_KEY_COLUMNS = ("date", "station", "split")


@dataclass(frozen=True)
class IntervalReferenceStateArtifacts:
    """Paths and input count for one non-formal source-reference run."""

    states_path: Path
    manifest_path: Path
    n_rows: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_canonical_prediction_input(predictions: pd.DataFrame) -> str:
    """Hash the exact prediction fields used by the source-reference mapping."""

    canonical = (
        predictions.loc[:, _PREDICTION_COLUMNS]
        .sort_values(["date", "station", "split"], kind="stable")
        .reset_index(drop=True)
    )
    buffer = io.StringIO()
    canonical.to_csv(
        buffer,
        index=False,
        lineterminator="\n",
        na_rep="<NA>",
        float_format="%.17g",
        date_format="%Y-%m-%d",
    )
    return hashlib.sha256(buffer.getvalue().encode("utf-8")).hexdigest()


def _normalize_prediction_identity(
    predictions: pd.DataFrame,
    *,
    context: str,
    require_all_splits: bool,
) -> pd.DataFrame:
    """Normalize and validate the identity fields for one prediction frame."""

    normalized = predictions.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized["station"] = normalized["station"].astype("string").str.strip()
    normalized["split"] = normalized["split"].astype("string").str.strip().str.lower()
    if normalized["date"].isna().any():
        raise ValueError(f"{context} contains an invalid date")
    if normalized["station"].isna().any() or normalized["station"].eq("").any():
        raise ValueError(f"{context} contains a missing station")
    unknown_splits = sorted(
        set(normalized["split"].dropna().unique()).difference(PREDICTION_SPLITS)
    )
    if unknown_splits or normalized["split"].isna().any():
        raise ValueError(
            f"{context} contains an unsupported split; expected only "
            f"{', '.join(PREDICTION_SPLITS)}"
        )
    duplicate_keys = normalized.duplicated(_NATURAL_KEY_COLUMNS, keep=False)
    if duplicate_keys.any():
        raise ValueError(
            f"{context} contains duplicate (date, station, split) prediction rows"
        )
    if require_all_splits:
        missing_splits = sorted(set(PREDICTION_SPLITS).difference(normalized["split"]))
        if missing_splits:
            raise ValueError(
                f"{context} is missing required split(s): " + ", ".join(missing_splits)
            )
    return normalized.sort_values(
        list(_NATURAL_KEY_COLUMNS), kind="stable"
    ).reset_index(drop=True)


def load_reference_predictions(predictions_path: str | Path) -> pd.DataFrame:
    """Load and validate every prediction row needed for observed states."""

    path = Path(predictions_path)
    try:
        predictions = pd.read_csv(path, usecols=list(_PREDICTION_COLUMNS))
    except FileNotFoundError as exc:
        raise ValueError(f"prediction file does not exist: {path}") from exc
    except ValueError as exc:
        required = ", ".join(_PREDICTION_COLUMNS)
        raise ValueError(
            f"prediction file must include interval state columns: {required}"
        ) from exc
    if predictions.empty:
        raise ValueError("prediction file contains no rows")

    return _normalize_prediction_identity(
        predictions,
        context="prediction file",
        require_all_splits=True,
    )


def build_reference_interval_states(predictions: pd.DataFrame) -> pd.DataFrame:
    """Map issued calibration/test forecasts and preserve fit as not applicable."""

    missing = set(_PREDICTION_COLUMNS).difference(predictions.columns)
    if missing:
        raise ValueError(
            f"prediction states input is missing required columns: {sorted(missing)}"
        )
    normalized = _normalize_prediction_identity(
        predictions,
        context="prediction states input",
        require_all_splits=False,
    )
    normalized[ARTIFACT_INPUT_STATUS_COLUMN] = normalized["split"].map(
        lambda split: "not_applicable" if split == FIT_SPLIT else "valid"
    )
    return classify_observed_interval_states(
        normalized,
        input_status_column=ARTIFACT_INPUT_STATUS_COLUMN,
    )


def write_reference_interval_states(
    *,
    predictions_path: str | Path = DEFAULT_PREDICTIONS_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> IntervalReferenceStateArtifacts:
    """Write auditable, non-formal Figure 5-1 interval-state records."""

    predictions = load_reference_predictions(predictions_path)
    protocol = load_protocol(protocol_path)
    protocol_sha256 = protocol_content_sha256(protocol)
    states = build_reference_interval_states(predictions)
    prediction_input_sha256 = _sha256_canonical_prediction_input(predictions)

    states = states.copy()
    metadata = {
        "protocol_id": protocol["protocol_id"],
        "protocol_version": protocol["protocol_version"],
        "protocol_status": protocol["status"],
        "protocol_content_sha256": protocol_sha256,
        "artifact_status": STATE_ARTIFACT_STATUS,
        "formal_warning_output": False,
        "prediction_input_sha256": prediction_input_sha256,
    }
    for column, value in reversed(tuple(metadata.items())):
        states.insert(0, column, value)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    states_path = out_dir / STATES_FILENAME
    manifest_path = out_dir / MANIFEST_FILENAME
    states.to_csv(states_path, index=False, date_format="%Y-%m-%d")

    split_counts = {
        split: int((states["split"] == split).sum()) for split in PREDICTION_SPLITS
    }
    manifest = {
        "artifact_kind": "ootang_interval_reference_states",
        "artifact_status": STATE_ARTIFACT_STATUS,
        "formal_warning_output": False,
        "protocol": {
            "id": protocol["protocol_id"],
            "version": protocol["protocol_version"],
            "status": protocol["status"],
            "content_sha256": protocol_sha256,
            "unresolved_item_ids": list(unresolved_item_ids(protocol)),
        },
        "mapping": {
            "basis": THESIS_FIGURE_5_1_MAPPING_ID,
            "semantic": protocol["confirmed"]["interval"]["semantic"],
            "not_claimed": protocol["confirmed"]["interval"]["not_claimed"],
            "fit_policy": "fit_rows_are_diagnostic_not_applicable",
            "issued_prediction_splits": list(ISSUED_SPLITS),
            "input_status_policy": (
                "forecast_predictions_csv_has_no_interval_source_status; "
                "fit_is_derived_not_applicable; calibration_and_test_are_valid_candidates"
            ),
        },
        "selection": {
            "n_rows": int(len(states)),
            "splits": list(PREDICTION_SPLITS),
            "split_counts": split_counts,
            "stations": sorted(states["station"].astype(str).unique().tolist()),
        },
        "source_predictions": {
            "path": str(Path(predictions_path)),
            "selected_columns": list(_PREDICTION_COLUMNS),
            "sha256": prediction_input_sha256,
            "canonicalization": "csv_utf8_lf_na_<NA>_float_%.17g_date_%Y-%m-%d",
        },
        "states": {
            "path": str(states_path),
            "sha256": _sha256_file(states_path),
            "columns": list(states.columns),
            "mapped_row_count": int(states["interval_status"].eq("valid").sum()),
            "not_applicable_row_count": int(
                states["interval_status"].eq("not_applicable").sum()
            ),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return IntervalReferenceStateArtifacts(
        states_path=states_path,
        manifest_path=manifest_path,
        n_rows=len(states),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write non-formal source-referenced observed interval states."
    )
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    artifacts = write_reference_interval_states(
        predictions_path=args.predictions,
        output_dir=args.output_dir,
        protocol_path=args.protocol,
    )
    print(
        "wrote source-referenced observed interval states "
        f"({artifacts.n_rows} rows): {artifacts.states_path}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PREDICTIONS_PATH",
    "ARTIFACT_INPUT_STATUS_COLUMN",
    "FIT_SPLIT",
    "ISSUED_SPLITS",
    "IntervalReferenceStateArtifacts",
    "MANIFEST_FILENAME",
    "PREDICTION_SPLITS",
    "STATE_ARTIFACT_STATUS",
    "STATES_FILENAME",
    "build_reference_interval_states",
    "load_reference_predictions",
    "write_reference_interval_states",
]
