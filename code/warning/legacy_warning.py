"""Explicit provenance labels for retained pre-revision warning artifacts."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pandas as pd


LEGACY_WARNING_PATH = "legacy_exploratory"
LEGACY_WARNING_METHOD_ID = "legacy_30_day_v0_four_level_primary_secondary_fusion"
FORMAL_WARNING_OUTPUT = False
LEGACY_WARNING_MANIFEST_SCHEMA_VERSION = 1


def legacy_warning_metadata() -> dict[str, str | bool]:
    """Return the immutable label shared by retained legacy outputs."""

    return {
        "warning_path": LEGACY_WARNING_PATH,
        "formal_warning_output": FORMAL_WARNING_OUTPUT,
        "warning_method_id": LEGACY_WARNING_METHOD_ID,
    }


def attach_legacy_warning_metadata(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy visibly marked as a non-formal legacy warning artifact."""

    result = frame.copy()
    for field, value in legacy_warning_metadata().items():
        result[field] = value
    return result


def write_legacy_warning_manifest(
    path: str | Path,
    *,
    producer: str,
    artifacts: Iterable[str | Path],
) -> None:
    """Write a sidecar that marks non-tabular legacy outputs as non-formal."""

    manifest_path = Path(path)
    payload = {
        "schema_version": LEGACY_WARNING_MANIFEST_SCHEMA_VERSION,
        **legacy_warning_metadata(),
        "producer": producer,
        "artifacts": [str(artifact) for artifact in artifacts],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
