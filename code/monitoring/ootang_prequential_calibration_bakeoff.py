"""Causal shadow bakeoff for Ootang prequential interval calibrators.

The runner reuses the fully validated E1 point-forecast stream and changes only
the interval calibrator.  It is retrospective research infrastructure: no
candidate is ranked, promoted, or connected to the live v1 ledger.  Every
same-date candidate interval is materialized before any outcome from that date
is passed to a calibrator.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable

import numpy as np
import pandas as pd
import sklearn


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_monitor as e1_monitor  # noqa: E402
from warning.draft_evidence import (  # noqa: E402
    FileReplacement,
    promote_staged_files,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = (
    ROOT / "config" / "ootang_prequential_calibration_bakeoff.v1.json"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "figures" / "prequential_calibration_bakeoff_ootang_v1"
)
EXPECTED_CONFIG_FILE_SHA256 = (
    "fb9db3e1e43a30d7d0b2becb3f1dee1e5bd474041ae2e7acdf51d2a8acf0a48b"
)
MANIFEST_SCHEMA_VERSION = "ootang_prequential_calibration_bakeoff_manifest_v1"
ARTIFACT_STATUS = "retrospective_prequential_calibration_bakeoff_not_confirmatory"
COMPARISON_STATUS = "retrospective_descriptive_no_ranking_no_promotion"
ZERO_HASH = "0" * 64
METHODS = ("aci_v1_control", "agaci_ewa_variant_v1", "spci_qrf_v1")
IDENTITY_COLUMNS = ("method", "fold", "date", "station")
ISSUE_SOURCE_COLUMNS = (
    "station",
    "issue_state_reset_reason",
    "issue_history_count",
    "issue_point_forecast_mm",
    "issue_interval_lower_mm",
    "issue_interval_upper_mm",
    "issue_aci_alpha",
    "issue_batch_sha256",
)
REVEAL_SOURCE_COLUMNS = (
    "station",
    "reveal_actual_mm",
    "reveal_state_reset_after_update",
)
FORBIDDEN_OUTPUT_TOKENS = (
    "winner",
    "selected",
    "optimal",
    "ranking",
    "warning_color",
    "event_recall",
    "false_alarm_rate",
    "far",
    "field_truth",
)


class CalibrationBakeoffConfigError(ValueError):
    """Raised when the immutable v1 bakeoff profile is not exact."""


class CalibrationBakeoffInputError(RuntimeError):
    """Raised when the protected E1 source bundle is missing or changed."""


class CalibrationBakeoffOutputError(RuntimeError):
    """Raised when a candidate result cannot be independently reproduced."""


@dataclass(frozen=True, slots=True)
class ValidatedE1Source:
    """Fully replayed E1 station stream and its immutable provenance."""

    station_timeline: pd.DataFrame
    site_timeline: pd.DataFrame
    metrics: pd.DataFrame
    manifest: dict[str, Any]
    artifact_records: dict[str, dict[str, Any]]


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"forbidden JSON numeric constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path, *, config: bool, label: str) -> dict[str, Any]:
    error_type = CalibrationBakeoffConfigError if config else CalibrationBakeoffInputError
    try:
        raw = path.read_bytes()
        payload = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise error_type(f"cannot load strict {label} JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise error_type(f"{label} must be a JSON object")
    return payload


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CalibrationBakeoffInputError(f"cannot hash required file: {path}") from exc
    return digest.hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _manifest_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _resolve_declared_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise CalibrationBakeoffConfigError(
            f"{label} must be a non-empty repository-relative path"
        )
    lexical = ROOT.joinpath(*Path(value).parts)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise CalibrationBakeoffInputError(f"required path is unavailable: {value}") from exc
    if resolved != lexical.absolute():
        raise CalibrationBakeoffInputError(
            f"required path must not traverse a symbolic-link alias: {value}"
        )
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise CalibrationBakeoffInputError(
            f"required path escapes the repository: {value}"
        ) from exc
    return resolved


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load only the byte-exact, predeclared v1 experimental profile."""

    path = Path(path).resolve()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CalibrationBakeoffConfigError(f"cannot read bakeoff config: {path}") from exc
    digest = _sha256_bytes(raw)
    if not hmac.compare_digest(digest, EXPECTED_CONFIG_FILE_SHA256):
        raise CalibrationBakeoffConfigError(
            "bakeoff config bytes do not match the immutable v1 profile"
        )
    profile = _load_json(path, config=True, label="bakeoff profile")
    if profile.get("schema_version") != (
        "ootang_prequential_calibration_bakeoff_profile_v1"
    ):
        raise CalibrationBakeoffConfigError("unsupported bakeoff profile schema")
    if tuple(profile.get("design", {}).get("candidate_order", ())) != METHODS:
        raise CalibrationBakeoffConfigError("candidate order does not match v1")
    if profile.get("artifact_status") != ARTIFACT_STATUS:
        raise CalibrationBakeoffConfigError("artifact status does not match v1")
    return profile


def _verify_declared_file(
    record: object,
    *,
    label: str,
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
        raise CalibrationBakeoffConfigError(
            f"source_contract.artifacts.{label} must contain path and sha256"
        )
    path = _resolve_declared_path(record["path"], label=label)
    expected = record["sha256"]
    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise CalibrationBakeoffConfigError(f"invalid {label} SHA-256 declaration")
    actual = _sha256_file(path)
    if not hmac.compare_digest(actual, expected):
        raise CalibrationBakeoffInputError(
            f"protected E1 {label} SHA-256 changed: expected {expected}, found {actual}"
        )
    return path, {
        "path": _manifest_path(path),
        "sha256": actual,
        "size_bytes": path.stat().st_size,
    }


def validate_e1_source(profile: dict[str, Any]) -> ValidatedE1Source:
    """Hash-check and mathematically replay the complete protected E1 bundle."""

    source_contract = profile["source_contract"]
    monitor_record = source_contract["monitor_profile"]
    monitor_path, monitor_provenance = _verify_declared_file(
        monitor_record, label="monitor_profile"
    )
    monitor_profile = e1_monitor.load_config(monitor_path)

    declared = source_contract["artifacts"]
    paths: dict[str, Path] = {}
    records: dict[str, dict[str, Any]] = {
        "monitor_profile": monitor_provenance
    }
    for name in ("station_timeline", "site_timeline", "metrics", "manifest"):
        paths[name], records[name] = _verify_declared_file(declared[name], label=name)

    bundle_dir = _resolve_declared_path(
        source_contract["bundle_directory"], label="bundle_directory"
    )
    expected_bundle_dir = paths["station_timeline"].parent
    if bundle_dir != expected_bundle_dir:
        raise CalibrationBakeoffInputError("E1 bundle directory/path declarations disagree")
    station, site, metrics = e1_monitor.read_materialized_bundle(
        bundle_dir, monitor_profile
    )
    manifest = _load_json(paths["manifest"], config=False, label="E1 manifest")
    if manifest.get("schema_version") != source_contract["manifest_schema_version"]:
        raise CalibrationBakeoffInputError("E1 manifest schema changed")
    if manifest.get("artifact_status") != (
        "retrospective_prequential_self_supervised_not_confirmatory"
    ):
        raise CalibrationBakeoffInputError("E1 artifact status changed")
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
    ):
        if manifest.get(flag) is not False:
            raise CalibrationBakeoffInputError(f"E1 manifest {flag} must remain false")

    expected_rows = {
        "station_timeline": source_contract["expected_station_rows"],
        "site_timeline": source_contract["expected_site_rows"],
        "metrics": source_contract["expected_metric_rows"],
    }
    frames = {
        "station_timeline": station,
        "site_timeline": site,
        "metrics": metrics,
    }
    for name, frame in frames.items():
        if len(frame) != expected_rows[name]:
            raise CalibrationBakeoffInputError(
                f"E1 {name} row count changed: {len(frame)}"
            )
        output_record = manifest.get("outputs", {}).get(name)
        if not isinstance(output_record, dict):
            raise CalibrationBakeoffInputError(
                f"E1 manifest is missing output record {name}"
            )
        if output_record.get("sha256") != records[name]["sha256"]:
            raise CalibrationBakeoffInputError(
                f"E1 manifest hash disagrees for {name}"
            )
        if output_record.get("rows") != len(frame):
            raise CalibrationBakeoffInputError(
                f"E1 manifest row declaration disagrees for {name}"
            )

    required_station_columns = {
        "fold",
        "date",
        "station",
        "issue_state_reset_reason",
        "issue_history_count",
        "issue_point_forecast_mm",
        "issue_interval_lower_mm",
        "issue_interval_upper_mm",
        "issue_aci_alpha",
        "issue_batch_sha256",
        "reveal_actual_mm",
        "reveal_state_reset_after_update",
    }
    if not required_station_columns.issubset(station.columns):
        missing = sorted(required_station_columns - set(station.columns))
        raise CalibrationBakeoffInputError(
            f"E1 station timeline lacks required columns: {missing}"
        )
    if station.duplicated(["fold", "date", "station"]).any():
        raise CalibrationBakeoffInputError("E1 station identity is not unique")
    numeric = station[["issue_point_forecast_mm", "reveal_actual_mm"]].to_numpy(
        dtype=float
    )
    if not np.isfinite(numeric).all():
        raise CalibrationBakeoffInputError("E1 point forecast/outcome contains nonfinite data")

    folds = tuple(int(value) for value in sorted(station["fold"].unique()))
    if folds != tuple(source_contract["folds"]):
        raise CalibrationBakeoffInputError("E1 fold identities changed")
    stations = tuple(source_contract["stations"])
    station_values = set(station["station"].astype(str))
    if station_values != set(stations):
        raise CalibrationBakeoffInputError("E1 station identities changed")
    per_fold_dates = station.groupby("fold")["date"].nunique()
    if not (per_fold_dates == int(source_contract["dates_per_fold"])).all():
        raise CalibrationBakeoffInputError("E1 dates-per-fold contract changed")
    date_station_counts = station.groupby(["fold", "date"])["station"].nunique()
    if not (date_station_counts == len(stations)).all():
        raise CalibrationBakeoffInputError("E1 date/station grid is incomplete")
    issue_hash_counts = station.groupby(["fold", "date"])[
        "issue_batch_sha256"
    ].nunique()
    if not (issue_hash_counts == 1).all():
        raise CalibrationBakeoffInputError("E1 issue batch binding is torn")

    order = {station_name: index for index, station_name in enumerate(stations)}
    ordered = station.assign(
        _station_order=station["station"].map(order),
        _date_order=pd.to_datetime(station["date"], errors="raise"),
    ).sort_values(["fold", "_date_order", "_station_order"], kind="stable")
    ordered = ordered.drop(columns=["_station_order", "_date_order"]).reset_index(
        drop=True
    )
    return ValidatedE1Source(
        station_timeline=ordered,
        site_timeline=site,
        metrics=metrics,
        manifest=manifest,
        artifact_records=records,
    )


def _flags(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_status": profile["artifact_status"],
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
    }


def _pinball_loss(actual: float, bound: float, tau: float) -> float:
    residual = actual - bound
    return float((tau - (1.0 if residual < 0.0 else 0.0)) * residual)


def _interval_reveal_fields(
    *,
    actual: float,
    lower: float | None,
    upper: float | None,
    target_miscoverage: float,
) -> dict[str, Any]:
    if lower is None or upper is None:
        if lower is not None or upper is not None:
            raise CalibrationBakeoffOutputError("candidate interval is partially available")
        return {
            "reveal_interval_covered": -1,
            "reveal_lower_miss": -1,
            "reveal_upper_miss": -1,
            "reveal_interval_width_mm": math.nan,
            "reveal_interval_score_80_mm": math.nan,
            "reveal_lower_pinball_loss": math.nan,
            "reveal_upper_pinball_loss": math.nan,
        }
    if not (math.isfinite(lower) and math.isfinite(upper)) or lower > upper:
        raise CalibrationBakeoffOutputError("candidate interval is nonfinite or crossing")
    lower_miss = actual < lower
    upper_miss = actual > upper
    covered = not lower_miss and not upper_miss
    width = upper - lower
    score = width
    if lower_miss:
        score += (2.0 / target_miscoverage) * (lower - actual)
    if upper_miss:
        score += (2.0 / target_miscoverage) * (actual - upper)
    return {
        "reveal_interval_covered": int(covered),
        "reveal_lower_miss": int(lower_miss),
        "reveal_upper_miss": int(upper_miss),
        "reveal_interval_width_mm": float(width),
        "reveal_interval_score_80_mm": float(score),
        "reveal_lower_pinball_loss": _pinball_loss(actual, lower, 0.1),
        "reveal_upper_pinball_loss": _pinball_loss(actual, upper, 0.9),
    }


def _candidate_issue_hash(
    previous_hash: str,
    *,
    fold: int,
    date: str,
    source_issue_hash: str,
    issues: Iterable[dict[str, Any]],
) -> str:
    payload = {
        "schema_version": "ootang_calibration_candidate_issue_batch_v1",
        "previous_candidate_issue_batch_sha256": previous_hash,
        "fold": fold,
        "date": date,
        "source_issue_batch_sha256": source_issue_hash,
        "issues": list(issues),
    }
    return _sha256_bytes(_canonical_json_bytes(payload))


def _timeline_columns(profile: dict[str, Any]) -> list[str]:
    gamma_count = len(profile["candidates"]["agaci_ewa_variant_v1"]["gamma_grid"])
    columns = [
        *IDENTITY_COLUMNS,
        "source_issue_batch_sha256",
        "issue_state_reset_reason",
        "issue_history_count",
        "issue_calibration_state_before_sha256",
        "issue_interval_available",
        "issue_uncertainty_action",
        "issue_point_forecast_mm",
        "issue_interval_lower_mm",
        "issue_interval_upper_mm",
        "issue_aci_alpha",
        "issue_agaci_effective_gamma_lower",
        "issue_agaci_effective_gamma_upper",
        "issue_agaci_lower_weight_entropy",
        "issue_agaci_upper_weight_entropy",
        "issue_agaci_crossing",
    ]
    columns.extend(f"issue_agaci_alpha_{index}" for index in range(gamma_count))
    columns.extend(
        f"issue_agaci_lower_weight_{index}" for index in range(gamma_count)
    )
    columns.extend(
        f"issue_agaci_upper_weight_{index}" for index in range(gamma_count)
    )
    columns.extend(
        [
            "issue_spci_beta",
            "issue_spci_training_pairs",
            "issue_spci_qrf_status",
            "previous_candidate_issue_batch_sha256",
            "candidate_issue_batch_sha256",
            "reveal_actual_mm",
            "reveal_signed_residual_mm",
            "reveal_interval_covered",
            "reveal_lower_miss",
            "reveal_upper_miss",
            "reveal_interval_width_mm",
            "reveal_interval_score_80_mm",
            "reveal_lower_pinball_loss",
            "reveal_upper_pinball_loss",
            "reveal_reset_after_update",
            "reveal_calibration_state_after_sha256",
            "artifact_status",
            "formal_warning_output",
            "independent_label_used",
            "confirmatory_external_validation",
            "vajont_used",
        ]
    )
    return columns


def build_candidate_timeline(
    source: ValidatedE1Source,
    profile: dict[str, Any],
) -> pd.DataFrame:
    """Issue all candidates causally, then reveal outcomes and update states."""

    # Imported lazily so source/config validation fails before a numerical model
    # can be fitted.  The pure module has no filesystem or wall-clock access.
    from monitoring import calibration_challengers as challengers

    return _build_candidate_timeline_with_core(source, profile, challengers)


def _build_candidate_timeline_with_core(
    source: ValidatedE1Source,
    profile: dict[str, Any],
    challengers: Any,
) -> pd.DataFrame:
    """Internal adapter kept separate for deterministic synthetic tests."""

    gamma_grid = tuple(
        float(value)
        for value in profile["candidates"]["agaci_ewa_variant_v1"]["gamma_grid"]
    )
    spci_profile = profile["candidates"]["spci_qrf_v1"]
    spci_settings = challengers.SpciSettings(
        beta_grid=tuple(float(value) for value in spci_profile["beta_grid"]),
        lag=int(spci_profile["residual_lag"]),
        minimum_pairs=int(spci_profile["minimum_qrf_pairs"]),
        n_estimators=int(spci_profile["n_estimators"]),
        max_depth=int(spci_profile["max_depth"]),
        min_samples_leaf=int(spci_profile["min_samples_leaf"]),
        max_features=float(spci_profile["max_features"]),
        bootstrap=bool(spci_profile["bootstrap"]),
        n_jobs=int(spci_profile["n_jobs"]),
        random_state=int(spci_profile["random_state"]),
    )

    def fresh_state(method: str, reason: str) -> Any:
        if method == "aci_v1_control":
            return challengers.new_aci_state(reset_reason=reason)
        if method == "agaci_ewa_variant_v1":
            return challengers.new_agaci_state(
                gamma_grid, reset_reason=reason
            )
        if method == "spci_qrf_v1":
            return challengers.new_spci_state(
                spci_settings, reset_reason=reason
            )
        raise CalibrationBakeoffOutputError(f"unknown candidate method: {method}")

    def issue_candidate(method: str, state: Any, point: float) -> Any:
        if method == "aci_v1_control":
            return challengers.issue_aci(state, point)
        if method == "agaci_ewa_variant_v1":
            return challengers.issue_agaci(state, point)
        if method == "spci_qrf_v1":
            return challengers.issue_spci(state, point, spci_settings)
        raise CalibrationBakeoffOutputError(f"unknown candidate method: {method}")

    def reveal_candidate(
        method: str, state: Any, issue: Any, actual: float
    ) -> Any:
        if method == "aci_v1_control":
            return challengers.reveal_aci(state, issue, actual)
        if method == "agaci_ewa_variant_v1":
            return challengers.reveal_agaci(state, issue, actual)
        if method == "spci_qrf_v1":
            return challengers.reveal_spci(state, issue, actual)
        raise CalibrationBakeoffOutputError(f"unknown candidate method: {method}")

    def optional_number(value: Any) -> float:
        return math.nan if value is None else float(value)

    flags = _flags(profile)
    target_alpha = float(profile["design"]["target_miscoverage"])
    states: dict[str, dict[str, Any]] = {method: {} for method in METHODS}
    records: list[dict[str, Any]] = []
    previous_candidate_hash = ZERO_HASH
    previous_fold: int | None = None
    source_frame = source.station_timeline

    for (fold_value, date_value), date_frame in source_frame.groupby(
        ["fold", "date"], sort=False
    ):
        fold = int(fold_value)
        date = str(date_value)
        date_frame = date_frame.reset_index(drop=True)
        if previous_fold != fold:
            if not (date_frame["issue_history_count"].astype(int) == 0).all():
                raise CalibrationBakeoffInputError(
                    "first issue of a fold must have zero history"
                )
            reasons = set(date_frame["issue_state_reset_reason"].astype(str))
            expected_reason = "initial_fold_start" if previous_fold is None else "fold_change"
            if reasons != {expected_reason}:
                raise CalibrationBakeoffInputError(
                    "first issue reset reason does not match the fold boundary"
                )
            states = {
                method: {
                    station: fresh_state(method, expected_reason)
                    for station in profile["source_contract"]["stations"]
                }
                for method in METHODS
            }
            previous_fold = fold

        issued: list[dict[str, Any]] = []
        issue_hash_payloads: list[dict[str, Any]] = []
        source_issue_hashes = set(date_frame["issue_batch_sha256"].astype(str))
        if len(source_issue_hashes) != 1:
            raise CalibrationBakeoffInputError("same-date E1 issue hash is torn")
        source_issue_hash = next(iter(source_issue_hashes))

        # Method-major then station-major order is fixed in the issue batch.
        # This issue-only materialization structurally excludes every reveal field;
        # the outcome lookup is not constructed until the batch hash exists.
        issue_source_rows = date_frame.loc[
            :, list(ISSUE_SOURCE_COLUMNS)
        ].to_dict(orient="records")
        for method in METHODS:
            for source_row in issue_source_rows:
                station = str(source_row["station"])
                state = states[method][station]
                source_history = int(source_row["issue_history_count"])
                source_reason = str(source_row["issue_state_reset_reason"])
                if state.history_count != source_history:
                    raise CalibrationBakeoffOutputError(
                        f"{method}/{station} history diverged from E1 reset schedule"
                    )
                if state.reset_reason != source_reason:
                    raise CalibrationBakeoffOutputError(
                        f"{method}/{station} reset reason diverged from E1"
                    )
                point = float(source_row["issue_point_forecast_mm"])
                issue = issue_candidate(method, state, point)
                if issue.interval_status == "fail_closed":
                    raise CalibrationBakeoffOutputError(
                        f"{method}/{fold}/{date}/{station} failed closed: "
                        f"{issue.failure_reason}"
                    )
                if issue.interval_status not in {
                    "abstain_rewarm",
                    "interval_available",
                }:
                    raise CalibrationBakeoffOutputError(
                        f"unsupported {method} issue status: {issue.interval_status}"
                    )
                available = issue.interval_status == "interval_available"
                lower = issue.interval_lower_mm
                upper = issue.interval_upper_mm

                if method == "aci_v1_control":
                    source_alpha = float(source_row["issue_aci_alpha"])
                    if issue.alpha != source_alpha:
                        raise CalibrationBakeoffOutputError(
                            "ACI control alpha is not exact E1 parity"
                        )
                    source_lower = source_row["issue_interval_lower_mm"]
                    source_upper = source_row["issue_interval_upper_mm"]
                    if available:
                        if not (
                            float(lower) == float(source_lower)
                            and float(upper) == float(source_upper)
                        ):
                            raise CalibrationBakeoffOutputError(
                                "ACI control interval is not exact E1 parity"
                            )
                    elif not (pd.isna(source_lower) and pd.isna(source_upper)):
                        raise CalibrationBakeoffOutputError(
                            "ACI control warm-up is not exact E1 parity"
                        )

                gamma_count = len(gamma_grid)
                agaci_alphas = (
                    tuple(float(value) for value in issue.expert_alphas)
                    if method == "agaci_ewa_variant_v1"
                    else (math.nan,) * gamma_count
                )
                agaci_lower_weights = (
                    tuple(float(value) for value in issue.lower_weights)
                    if method == "agaci_ewa_variant_v1"
                    else (math.nan,) * gamma_count
                )
                agaci_upper_weights = (
                    tuple(float(value) for value in issue.upper_weights)
                    if method == "agaci_ewa_variant_v1"
                    else (math.nan,) * gamma_count
                )
                if method == "agaci_ewa_variant_v1" and (
                    len(agaci_alphas) != gamma_count
                    or len(agaci_lower_weights) != gamma_count
                    or len(agaci_upper_weights) != gamma_count
                ):
                    raise CalibrationBakeoffOutputError(
                        "AgACI diagnostics do not match the declared gamma grid"
                    )

                row: dict[str, Any] = {
                    "method": method,
                    "fold": fold,
                    "date": date,
                    "station": station,
                    "source_issue_batch_sha256": source_issue_hash,
                    "issue_state_reset_reason": issue.state_reset_reason,
                    "issue_history_count": int(issue.history_count),
                    "issue_calibration_state_before_sha256": (
                        issue.state_before_sha256
                    ),
                    "issue_interval_available": available,
                    "issue_uncertainty_action": issue.interval_status,
                    "issue_point_forecast_mm": point,
                    "issue_interval_lower_mm": optional_number(lower),
                    "issue_interval_upper_mm": optional_number(upper),
                    "issue_aci_alpha": (
                        float(issue.alpha)
                        if method == "aci_v1_control"
                        else math.nan
                    ),
                    "issue_agaci_effective_gamma_lower": (
                        float(issue.effective_gamma_lower)
                        if method == "agaci_ewa_variant_v1"
                        else math.nan
                    ),
                    "issue_agaci_effective_gamma_upper": (
                        float(issue.effective_gamma_upper)
                        if method == "agaci_ewa_variant_v1"
                        else math.nan
                    ),
                    "issue_agaci_lower_weight_entropy": (
                        float(issue.lower_weight_entropy)
                        if method == "agaci_ewa_variant_v1"
                        else math.nan
                    ),
                    "issue_agaci_upper_weight_entropy": (
                        float(issue.upper_weight_entropy)
                        if method == "agaci_ewa_variant_v1"
                        else math.nan
                    ),
                    "issue_agaci_crossing": False,
                    "issue_spci_beta": (
                        optional_number(issue.selected_beta)
                        if method == "spci_qrf_v1"
                        else math.nan
                    ),
                    "issue_spci_training_pairs": (
                        int(issue.training_pair_count)
                        if method == "spci_qrf_v1"
                        else -1
                    ),
                    "issue_spci_qrf_status": (
                        issue.interval_status
                        if method == "spci_qrf_v1"
                        else "not_applicable"
                    ),
                    **flags,
                }
                row.update(
                    {
                        f"issue_agaci_alpha_{index}": agaci_alphas[index]
                        for index in range(gamma_count)
                    }
                )
                row.update(
                    {
                        f"issue_agaci_lower_weight_{index}": (
                            agaci_lower_weights[index]
                        )
                        for index in range(gamma_count)
                    }
                )
                row.update(
                    {
                        f"issue_agaci_upper_weight_{index}": (
                            agaci_upper_weights[index]
                        )
                        for index in range(gamma_count)
                    }
                )
                issue_payload = {
                    "method": method,
                    "fold": fold,
                    "date": date,
                    "station": station,
                    "source_issue_batch_sha256": source_issue_hash,
                    "state_before_sha256": issue.state_before_sha256,
                    "state_reset_reason": issue.state_reset_reason,
                    "history_count": int(issue.history_count),
                    "interval_status": issue.interval_status,
                    "point_forecast_mm": point,
                    "interval_lower_mm": lower,
                    "interval_upper_mm": upper,
                    "aci_alpha": (
                        float(issue.alpha)
                        if method == "aci_v1_control"
                        else None
                    ),
                    "agaci_expert_alphas": (
                        list(agaci_alphas)
                        if method == "agaci_ewa_variant_v1"
                        else None
                    ),
                    "agaci_lower_weights": (
                        list(agaci_lower_weights)
                        if method == "agaci_ewa_variant_v1"
                        else None
                    ),
                    "agaci_upper_weights": (
                        list(agaci_upper_weights)
                        if method == "agaci_ewa_variant_v1"
                        else None
                    ),
                    "spci_beta": (
                        issue.selected_beta if method == "spci_qrf_v1" else None
                    ),
                    "spci_training_pairs": (
                        int(issue.training_pair_count)
                        if method == "spci_qrf_v1"
                        else None
                    ),
                }
                issued.append(
                    {
                        "method": method,
                        "station": station,
                        "state": state,
                        "issue": issue,
                        "row": row,
                    }
                )
                issue_hash_payloads.append(issue_payload)

        candidate_hash = _candidate_issue_hash(
            previous_candidate_hash,
            fold=fold,
            date=date,
            source_issue_hash=source_issue_hash,
            issues=issue_hash_payloads,
        )
        for item in issued:
            item["row"]["previous_candidate_issue_batch_sha256"] = (
                previous_candidate_hash
            )
            item["row"]["candidate_issue_batch_sha256"] = candidate_hash

        # Only after all 24 issue DTOs and the issue-only hash exist may any
        # same-date outcome be read and supplied to a method state.
        reveal_rows = (
            date_frame.loc[:, list(REVEAL_SOURCE_COLUMNS)]
            .set_index("station", verify_integrity=True)
            .to_dict(orient="index")
        )
        for item in issued:
            method = item["method"]
            station = item["station"]
            reveal_row = reveal_rows[station]
            state = item["state"]
            issue = item["issue"]
            row = item["row"]
            actual = float(reveal_row["reveal_actual_mm"])
            reveal = reveal_candidate(method, state, issue, actual)
            if reveal.actual_mm != actual:
                raise CalibrationBakeoffOutputError("candidate reveal actual changed")
            expected_signed_residual = actual - float(issue.point_forecast_mm)
            if reveal.signed_residual_mm != expected_signed_residual:
                raise CalibrationBakeoffOutputError(
                    "candidate signed residual does not match fixed point forecast"
                )
            reveal_fields = _interval_reveal_fields(
                actual=actual,
                lower=issue.interval_lower_mm,
                upper=issue.interval_upper_mm,
                target_miscoverage=target_alpha,
            )
            if reveal.interval_covered is None:
                if reveal_fields["reveal_interval_covered"] != -1:
                    raise CalibrationBakeoffOutputError(
                        "candidate reveal availability is inconsistent"
                    )
            elif int(reveal.interval_covered) != reveal_fields[
                "reveal_interval_covered"
            ]:
                raise CalibrationBakeoffOutputError(
                    "candidate reveal coverage is inconsistent"
                )
            reset = bool(reveal_row["reveal_state_reset_after_update"])
            next_state = (
                fresh_state(method, "drift_detected_previous_date")
                if reset
                else reveal.updated_state
            )
            states[method][station] = next_state
            row.update(
                {
                    "reveal_actual_mm": actual,
                    "reveal_signed_residual_mm": expected_signed_residual,
                    **reveal_fields,
                    "reveal_reset_after_update": reset,
                    "reveal_calibration_state_after_sha256": (
                        challengers.state_sha256(next_state)
                    ),
                }
            )
            records.append(row)
        previous_candidate_hash = candidate_hash

    timeline = pd.DataFrame.from_records(records)
    method_order = {method: index for index, method in enumerate(METHODS)}
    station_order = {
        station: index
        for index, station in enumerate(profile["source_contract"]["stations"])
    }
    timeline = timeline.assign(
        _method_order=timeline["method"].map(method_order),
        _station_order=timeline["station"].map(station_order),
        _date_order=pd.to_datetime(timeline["date"], errors="raise"),
    ).sort_values(
        ["_method_order", "fold", "_date_order", "_station_order"],
        kind="stable",
    )
    timeline = timeline.drop(
        columns=["_method_order", "_station_order", "_date_order"]
    ).reset_index(drop=True)
    return timeline.loc[:, _timeline_columns(profile)]


def _scope_frames(
    frame: pd.DataFrame,
    profile: dict[str, Any],
) -> Iterable[tuple[str, str, pd.DataFrame]]:
    yield "overall", "all", frame
    for station in profile["source_contract"]["stations"]:
        yield "station", station, frame.loc[frame["station"] == station]
    for block, stations in profile["evaluation"]["spatial_blocks"].items():
        yield "spatial_block", block, frame.loc[frame["station"].isin(stations)]


def _maximum_miss_streak(frame: pd.DataFrame) -> int:
    longest = 0
    active = frame.loc[frame["issue_interval_available"]].copy()
    for _, station_frame in active.groupby("station", sort=False):
        current = 0
        for covered in station_frame.sort_values("date")[
            "reveal_interval_covered"
        ].to_numpy(dtype=int):
            if covered == 0:
                current += 1
                longest = max(longest, current)
            else:
                current = 0
    return longest


def _rolling_coverage_max_abs_gap(
    frame: pd.DataFrame,
    *,
    target: float,
    window: int,
) -> float:
    active = frame.loc[frame["issue_interval_available"]]
    if active.empty:
        return math.nan
    daily = active.groupby("date", sort=True)["reveal_interval_covered"].mean()
    rolling = daily.rolling(window=window, min_periods=window).mean()
    if not rolling.notna().any():
        return math.nan
    return float((rolling - target).abs().max())


def build_candidate_metrics(
    timeline: pd.DataFrame,
    profile: dict[str, Any],
) -> pd.DataFrame:
    """Summarize availability, coverage, efficiency, and temporal stability."""

    target = float(profile["design"]["target_coverage"])
    rolling_window = int(profile["evaluation"]["rolling_coverage_window_dates"])
    records: list[dict[str, Any]] = []
    flags = _flags(profile)
    for method in METHODS:
        method_frame = timeline.loc[timeline["method"] == method]
        for fold in profile["source_contract"]["folds"]:
            fold_frame = method_frame.loc[method_frame["fold"] == fold]
            for scope, group, scoped in _scope_frames(fold_frame, profile):
                active = scoped.loc[scoped["issue_interval_available"]]
                planned_rows = len(scoped)
                interval_rows = len(active)
                if interval_rows:
                    coverage = float(active["reveal_interval_covered"].mean())
                    widths = active["reveal_interval_width_mm"].to_numpy(dtype=float)
                    mean_width = float(np.mean(widths))
                    median_width = float(np.median(widths))
                    p90_width = float(np.quantile(widths, 0.9, method="higher"))
                    mean_score = float(active["reveal_interval_score_80_mm"].mean())
                    lower_pinball = float(active["reveal_lower_pinball_loss"].mean())
                    upper_pinball = float(active["reveal_upper_pinball_loss"].mean())
                    lower_miss_rate = float(active["reveal_lower_miss"].mean())
                    upper_miss_rate = float(active["reveal_upper_miss"].mean())
                else:
                    coverage = mean_width = median_width = p90_width = math.nan
                    mean_score = lower_pinball = upper_pinball = math.nan
                    lower_miss_rate = upper_miss_rate = math.nan
                records.append(
                    {
                        "method": method,
                        "fold": int(fold),
                        "scope": scope,
                        "group": group,
                        "planned_rows": planned_rows,
                        "interval_rows": interval_rows,
                        "availability_rate": (
                            interval_rows / planned_rows if planned_rows else math.nan
                        ),
                        "abstention_rate": (
                            1.0 - interval_rows / planned_rows
                            if planned_rows
                            else math.nan
                        ),
                        "empirical_coverage": coverage,
                        "coverage_minus_target": coverage - target,
                        "absolute_coverage_gap": abs(coverage - target),
                        "lower_miss_rate": lower_miss_rate,
                        "upper_miss_rate": upper_miss_rate,
                        "mean_interval_width_mm": mean_width,
                        "median_interval_width_mm": median_width,
                        "p90_interval_width_mm": p90_width,
                        "mean_interval_score_80_mm": mean_score,
                        "mean_lower_pinball_loss": lower_pinball,
                        "mean_upper_pinball_loss": upper_pinball,
                        "rolling_30_date_coverage_max_abs_gap": (
                            _rolling_coverage_max_abs_gap(
                                scoped, target=target, window=rolling_window
                            )
                        ),
                        "longest_station_miss_streak": _maximum_miss_streak(scoped),
                        "reset_count": int(
                            scoped["reveal_reset_after_update"].astype(bool).sum()
                        ),
                        "crossing_count": int(
                            (
                                scoped["issue_interval_available"]
                                & (
                                    scoped["issue_interval_lower_mm"]
                                    > scoped["issue_interval_upper_mm"]
                                )
                            ).sum()
                        ),
                        **flags,
                    }
                )
    return pd.DataFrame.from_records(records)


def _paired_metric(
    frame: pd.DataFrame,
    column: str,
    method_suffix: str,
) -> float:
    values = frame[f"{column}_{method_suffix}"].to_numpy(dtype=float)
    return float(np.mean(values)) if len(values) else math.nan


def build_pairwise_comparison(
    timeline: pd.DataFrame,
    profile: dict[str, Any],
) -> pd.DataFrame:
    """Compare challengers to ACI without ranking or retrospective promotion."""

    control = str(profile["evaluation"]["pairwise_control"])
    target = float(profile["design"]["target_coverage"])
    key = ["fold", "date", "station"]
    metric_columns = [
        *key,
        "issue_interval_available",
        "reveal_interval_covered",
        "reveal_interval_width_mm",
        "reveal_interval_score_80_mm",
        "reveal_lower_pinball_loss",
        "reveal_upper_pinball_loss",
    ]
    control_frame = timeline.loc[timeline["method"] == control, metric_columns]
    records: list[dict[str, Any]] = []
    flags = _flags(profile)
    for challenger in METHODS:
        if challenger == control:
            continue
        challenger_frame = timeline.loc[
            timeline["method"] == challenger, metric_columns
        ]
        paired = control_frame.merge(
            challenger_frame,
            on=key,
            how="inner",
            validate="one_to_one",
            suffixes=("_control", "_challenger"),
        )
        if len(paired) != len(control_frame):
            raise CalibrationBakeoffOutputError("pairwise identity support is incomplete")
        for fold in profile["source_contract"]["folds"]:
            fold_frame = paired.loc[paired["fold"] == fold]
            for scope, group, scoped in _scope_frames(fold_frame, profile):
                control_available = scoped["issue_interval_available_control"].astype(bool)
                challenger_available = scoped[
                    "issue_interval_available_challenger"
                ].astype(bool)
                common = scoped.loc[control_available & challenger_available]
                base = {
                    "control_method": control,
                    "challenger_method": challenger,
                    "fold": int(fold),
                    "scope": scope,
                    "group": group,
                    "planned_rows": len(scoped),
                    "control_interval_rows": int(control_available.sum()),
                    "challenger_interval_rows": int(challenger_available.sum()),
                    "common_interval_rows": len(common),
                    "control_availability_rate": float(control_available.mean()),
                    "challenger_availability_rate": float(
                        challenger_available.mean()
                    ),
                    "availability_rate_difference": float(
                        challenger_available.mean() - control_available.mean()
                    ),
                    "comparison_status": COMPARISON_STATUS,
                    **flags,
                }
                records.append(
                    {
                        **base,
                        "support": "planned_population",
                        "control_empirical_coverage": math.nan,
                        "challenger_empirical_coverage": math.nan,
                        "coverage_difference": math.nan,
                        "control_absolute_coverage_gap": math.nan,
                        "challenger_absolute_coverage_gap": math.nan,
                        "absolute_coverage_gap_difference": math.nan,
                        "control_mean_interval_width_mm": math.nan,
                        "challenger_mean_interval_width_mm": math.nan,
                        "mean_interval_width_difference_mm": math.nan,
                        "control_mean_interval_score_80_mm": math.nan,
                        "challenger_mean_interval_score_80_mm": math.nan,
                        "mean_interval_score_difference_mm": math.nan,
                        "control_mean_total_pinball_loss": math.nan,
                        "challenger_mean_total_pinball_loss": math.nan,
                        "mean_total_pinball_loss_difference": math.nan,
                    }
                )
                control_coverage = _paired_metric(
                    common, "reveal_interval_covered", "control"
                )
                challenger_coverage = _paired_metric(
                    common, "reveal_interval_covered", "challenger"
                )
                control_width = _paired_metric(
                    common, "reveal_interval_width_mm", "control"
                )
                challenger_width = _paired_metric(
                    common, "reveal_interval_width_mm", "challenger"
                )
                control_score = _paired_metric(
                    common, "reveal_interval_score_80_mm", "control"
                )
                challenger_score = _paired_metric(
                    common, "reveal_interval_score_80_mm", "challenger"
                )
                control_pinball = _paired_metric(
                    common, "reveal_lower_pinball_loss", "control"
                ) + _paired_metric(common, "reveal_upper_pinball_loss", "control")
                challenger_pinball = _paired_metric(
                    common, "reveal_lower_pinball_loss", "challenger"
                ) + _paired_metric(
                    common, "reveal_upper_pinball_loss", "challenger"
                )
                records.append(
                    {
                        **base,
                        "support": "common_interval_support",
                        "control_empirical_coverage": control_coverage,
                        "challenger_empirical_coverage": challenger_coverage,
                        "coverage_difference": challenger_coverage - control_coverage,
                        "control_absolute_coverage_gap": abs(
                            control_coverage - target
                        ),
                        "challenger_absolute_coverage_gap": abs(
                            challenger_coverage - target
                        ),
                        "absolute_coverage_gap_difference": abs(
                            challenger_coverage - target
                        )
                        - abs(control_coverage - target),
                        "control_mean_interval_width_mm": control_width,
                        "challenger_mean_interval_width_mm": challenger_width,
                        "mean_interval_width_difference_mm": (
                            challenger_width - control_width
                        ),
                        "control_mean_interval_score_80_mm": control_score,
                        "challenger_mean_interval_score_80_mm": challenger_score,
                        "mean_interval_score_difference_mm": (
                            challenger_score - control_score
                        ),
                        "control_mean_total_pinball_loss": control_pinball,
                        "challenger_mean_total_pinball_loss": challenger_pinball,
                        "mean_total_pinball_loss_difference": (
                            challenger_pinball - control_pinball
                        ),
                    }
                )
    return pd.DataFrame.from_records(records)


def _assert_frame_equal(
    actual: pd.DataFrame,
    expected: pd.DataFrame,
    *,
    label: str,
) -> None:
    try:
        pd.testing.assert_frame_equal(
            actual.reset_index(drop=True),
            expected.reset_index(drop=True),
            check_exact=True,
            check_dtype=False,
        )
    except AssertionError as exc:
        raise CalibrationBakeoffOutputError(
            f"materialized {label} does not match deterministic replay"
        ) from exc


def validate_outputs(
    timeline: pd.DataFrame,
    metrics: pd.DataFrame,
    pairwise: pd.DataFrame,
    source: ValidatedE1Source,
    profile: dict[str, Any],
    *,
    replay_timeline: bool = True,
) -> None:
    """Reject malformed, selective, ranked, or non-reproducible outputs."""

    expected_columns = _timeline_columns(profile)
    if list(timeline.columns) != expected_columns:
        raise CalibrationBakeoffOutputError("candidate timeline columns changed")
    if len(timeline) != len(source.station_timeline) * len(METHODS):
        raise CalibrationBakeoffOutputError("candidate timeline row count changed")
    if timeline.duplicated(list(IDENTITY_COLUMNS)).any():
        raise CalibrationBakeoffOutputError("candidate timeline identity is not unique")
    if tuple(timeline["method"].drop_duplicates()) != METHODS:
        raise CalibrationBakeoffOutputError("candidate method order changed")
    output_names = [str(column).lower() for frame in (timeline, metrics, pairwise) for column in frame.columns]
    for token in FORBIDDEN_OUTPUT_TOKENS:
        if any(token == name or name.startswith(f"{token}_") for name in output_names):
            raise CalibrationBakeoffOutputError(
                f"prohibited retrospective selection/alert field: {token}"
            )
    if not np.array_equal(
        timeline["issue_point_forecast_mm"].to_numpy(dtype=float),
        np.tile(
            source.station_timeline["issue_point_forecast_mm"].to_numpy(dtype=float),
            len(METHODS),
        ),
    ):
        raise CalibrationBakeoffOutputError("candidate point forecasts are not exact E1 parity")
    active = timeline["issue_interval_available"].astype(bool)
    if (
        timeline.loc[active, "issue_interval_lower_mm"]
        > timeline.loc[active, "issue_interval_upper_mm"]
    ).any():
        raise CalibrationBakeoffOutputError("candidate interval crossing is present")
    if timeline.loc[~active, ["issue_interval_lower_mm", "issue_interval_upper_mm"]].notna().any().any():
        raise CalibrationBakeoffOutputError("abstained rows contain interval bounds")
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
    ):
        for frame in (timeline, metrics, pairwise):
            if flag not in frame or frame[flag].astype(bool).any():
                raise CalibrationBakeoffOutputError(f"output flag {flag} must remain false")
    expected_metrics = build_candidate_metrics(timeline, profile)
    expected_pairwise = build_pairwise_comparison(timeline, profile)
    _assert_frame_equal(metrics, expected_metrics, label="candidate metrics")
    _assert_frame_equal(pairwise, expected_pairwise, label="pairwise comparison")
    if replay_timeline:
        expected_timeline = build_candidate_timeline(source, profile)
        _assert_frame_equal(timeline, expected_timeline, label="candidate timeline")


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, lineterminator="\n", float_format="%.17g")


def _read_csv(path: Path, *, label: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path, float_precision="round_trip")
    except (OSError, UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise CalibrationBakeoffOutputError(
            f"cannot reload staged {label} with round-trip precision"
        ) from exc


def _output_record(path: Path, target: Path, frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "path": _manifest_path(target),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "rows": len(frame),
        "columns": list(frame.columns),
    }


def _cross_fold_diagnostics(
    metrics: pd.DataFrame,
    profile: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for method in METHODS:
        method_metrics = metrics.loc[metrics["method"] == method]
        overall = method_metrics.loc[
            (method_metrics["scope"] == "overall")
            & (method_metrics["group"] == "all")
        ].sort_values("fold")
        station = method_metrics.loc[method_metrics["scope"] == "station"]
        blocks = method_metrics.loc[method_metrics["scope"] == "spatial_block"]
        coverages = overall["empirical_coverage"].to_numpy(dtype=float)
        result[method] = {
            "fold_coverage": [float(value) for value in coverages],
            "fold_coverage_standard_deviation": float(
                np.std(coverages, ddof=0)
            ),
            "worst_station_absolute_coverage_gap": float(
                station["absolute_coverage_gap"].max()
            ),
            "worst_spatial_block_absolute_coverage_gap": float(
                blocks["absolute_coverage_gap"].max()
            ),
            "mean_interval_score_80_mm_by_fold": [
                float(value)
                for value in overall["mean_interval_score_80_mm"].to_numpy(
                    dtype=float
                )
            ],
            "descriptive_only": True,
        }
    return result


def _build_manifest(
    *,
    profile: dict[str, Any],
    config_path: Path,
    source: ValidatedE1Source,
    timeline: pd.DataFrame,
    metrics: pd.DataFrame,
    outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifact_kind": profile["artifact_kind"],
        **_flags(profile),
        "case": "ootang",
        "default_pipeline_member": False,
        "profile": {
            "id": profile["profile_id"],
            "version": profile["profile_version"],
            "path": _manifest_path(config_path),
            "file_sha256": _sha256_file(config_path),
            "content_sha256": _sha256_bytes(_canonical_json_bytes(profile)),
        },
        "implementation": {
            "runner": {
                "path": _manifest_path(Path(__file__)),
                "sha256": _sha256_file(Path(__file__)),
            },
            "calibration_core": {
                "path": "code/monitoring/calibration_challengers.py",
                "sha256": _sha256_file(
                    ROOT / "code" / "monitoring" / "calibration_challengers.py"
                ),
            },
            "e1_validator": {
                "path": "code/monitoring/ootang_prequential_monitor.py",
                "sha256": _sha256_file(
                    ROOT / "code" / "monitoring" / "ootang_prequential_monitor.py"
                ),
            },
            "atomic_promotion_helper": {
                "path": "code/warning/draft_evidence.py",
                "sha256": _sha256_file(
                    ROOT / "code" / "warning" / "draft_evidence.py"
                ),
            },
            "project": {
                "path": "pyproject.toml",
                "sha256": _sha256_file(ROOT / "pyproject.toml"),
            },
            "dependency_lock": {
                "path": "uv.lock",
                "sha256": _sha256_file(ROOT / "uv.lock"),
            },
        },
        "runtime": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "source": {
            "kind": "fully_replayed_protected_e1_station_stream",
            "artifacts": source.artifact_records,
            "point_forecast_rows": len(source.station_timeline),
            "point_forecast_parity_exact": True,
            "e1_issue_chain_terminal_sha256": str(
                source.station_timeline.iloc[-1]["issue_batch_sha256"]
            ),
        },
        "design": profile["design"],
        "candidates": profile["candidates"],
        "evaluation": profile["evaluation"],
        "causality": {
            "same_date_issue_before_reveal": True,
            "current_actual_excluded_from_issue_hash": True,
            "candidate_state_updates_after_reveal_only": True,
            "fold_state_shared": False,
            "station_state_shared": False,
            "e1_reset_schedule_reused": True,
            "future_actual_mutation_preserves_issue_prefix": True,
        },
        "candidate_issue_hash_chain": {
            "schema_version": "ootang_calibration_candidate_issue_batch_v1",
            "initial_previous_hash": ZERO_HASH,
            "terminal_hash": str(timeline.iloc[-1]["candidate_issue_batch_sha256"]),
            "binds_source_issue_batch_sha256": True,
            "outcome_fields_excluded": True,
        },
        "cross_fold_diagnostics": _cross_fold_diagnostics(metrics, profile),
        "claims": profile["claims"],
        "materialized_validation": {
            "csv_float_format": "%.17g",
            "csv_read_float_precision": "round_trip",
            "staged_csv_reloaded_before_promotion": True,
            "candidate_timeline_deterministically_replayed_from_source": True,
            "independent_implementation_replay_performed": False,
            "metrics_recomputed_from_reloaded_timeline": True,
            "pairwise_recomputed_from_reloaded_timeline": True,
            "bundle_wide_sigkill_transaction": False,
            "selection_performed": False,
            "promotion_performed": False,
        },
        "outputs": outputs,
    }


def write_calibration_bakeoff(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> Path:
    """Validate E1, run candidates, replay staged bytes, and atomically publish."""

    config_path = Path(config_path).resolve()
    profile = load_config(config_path)
    source = validate_e1_source(profile)
    timeline = build_candidate_timeline(source, profile)
    metrics = build_candidate_metrics(timeline, profile)
    pairwise = build_pairwise_comparison(timeline, profile)
    validate_outputs(
        timeline,
        metrics,
        pairwise,
        source,
        profile,
        replay_timeline=False,
    )

    output_value = profile["outputs"]["directory"]
    output_dir = ROOT.joinpath(*Path(output_value).parts).absolute()
    if output_dir != DEFAULT_OUTPUT_DIR.absolute():
        raise CalibrationBakeoffConfigError("v1 output directory changed")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    names = profile["outputs"]
    targets = {
        name: output_dir / names[name]
        for name in (
            "candidate_timeline",
            "candidate_metrics",
            "pairwise_comparison",
            "manifest",
        )
    }
    with tempfile.TemporaryDirectory(
        prefix=".ootang-calibration-bakeoff-", dir=output_dir.parent
    ) as directory:
        staging = Path(directory)
        staged = {
            name: staging / names[name]
            for name in (
                "candidate_timeline",
                "candidate_metrics",
                "pairwise_comparison",
            )
        }
        frames = {
            "candidate_timeline": timeline,
            "candidate_metrics": metrics,
            "pairwise_comparison": pairwise,
        }
        for name, frame in frames.items():
            _write_csv(frame, staged[name])
        materialized = {
            name: _read_csv(staged[name], label=name) for name in staged
        }
        validate_outputs(
            materialized["candidate_timeline"],
            materialized["candidate_metrics"],
            materialized["pairwise_comparison"],
            source,
            profile,
            replay_timeline=True,
        )
        output_records = {
            name: _output_record(staged[name], targets[name], materialized[name])
            for name in staged
        }
        manifest = _build_manifest(
            profile=profile,
            config_path=config_path,
            source=source,
            timeline=materialized["candidate_timeline"],
            metrics=materialized["candidate_metrics"],
            outputs=output_records,
        )
        staged_manifest = staging / names["manifest"]
        staged_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        promote_staged_files(
            tuple(
                [
                    FileReplacement(staged[name], targets[name])
                    for name in (
                        "candidate_timeline",
                        "candidate_metrics",
                        "pairwise_comparison",
                    )
                ]
                + [FileReplacement(staged_manifest, targets["manifest"])]
            )
        )
    return targets["manifest"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the explicit retrospective Ootang calibration bakeoff. "
            "The immutable profile controls all inputs, outputs, and methods."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        manifest = write_calibration_bakeoff(config_path=args.config)
    except (
        CalibrationBakeoffConfigError,
        CalibrationBakeoffInputError,
        CalibrationBakeoffOutputError,
        e1_monitor.PrequentialConfigError,
        e1_monitor.PrequentialInputError,
        e1_monitor.PrequentialOutputError,
    ) as exc:
        print(f"calibration bakeoff blocked: {exc}", file=sys.stderr)
        return 2
    print(f"calibration bakeoff manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CalibrationBakeoffConfigError",
    "CalibrationBakeoffInputError",
    "CalibrationBakeoffOutputError",
    "ValidatedE1Source",
    "build_candidate_metrics",
    "build_candidate_timeline",
    "build_pairwise_comparison",
    "load_config",
    "validate_e1_source",
    "validate_outputs",
    "write_calibration_bakeoff",
]
