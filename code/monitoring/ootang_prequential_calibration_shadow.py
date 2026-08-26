"""Machine-only prospective calibration shadow over the verified E2-A ledger.

The shadow has an independent append-only ledger.  It never edits the live v1
ledger, never reads a same-target outcome while issuing intervals, and never
promotes a calibrator.  Every public shadow read reconstructs all three
calibrator states from genesis with the pure calibration core.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_prequential_calibration_shadow.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "28c02510f81e1832220913d4bfde69bc8abe9a2269aa8279aabc297f60113857"
)
TEST_OVERRIDE_ENV = "OOTANG_CALIBRATION_SHADOW_ALLOW_TEST_OVERRIDE"
ZERO_HASH = "0" * 64
HEX_DIGITS = frozenset("0123456789abcdef")
METHODS = ("aci_v1_control", "agaci_ewa_variant_v1", "spci_qrf_v1")
STATIONS = ("ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9")
PAIR_ORDER = tuple((method, station) for method in METHODS for station in STATIONS)
MAX_ACTIONS_PER_RECONCILE = 512
ISSUE_EVENT_TYPES = frozenset(
    {
        "shadow_issue_batch_opened",
        "shadow_candidate_issued",
        "shadow_issue_batch_sealed",
    }
)
WAITING_STATUSES = frozenset(
    {
        "waiting_for_live_prerequisites",
        "waiting_for_live_issue",
        "waiting_for_live_outcome",
    }
)
SUCCESS_STATUSES = frozenset({"reconciled", "work_remaining", *WAITING_STATUSES})
FORBIDDEN_ISSUE_KEY_TOKENS = (
    "actual",
    "outcome",
    "reveal",
    "source_revision",
    "finalized_at",
)


class CalibrationShadowError(RuntimeError):
    """Base class for fail-closed calibration-shadow operations."""


class CalibrationShadowBusy(CalibrationShadowError):
    """Another upstream or shadow writer owns a required machine lock."""


class CalibrationShadowConfig(CalibrationShadowError):
    """The reviewed profile or one of its exact artifact bindings changed."""


class CalibrationShadowIntegrity(CalibrationShadowError):
    """Ledger, lifecycle, live binding, or mathematical replay failed."""


@dataclass(frozen=True)
class ShadowRuntimePaths:
    root: Path
    ledger: Path
    status: Path
    lock: Path


@dataclass(frozen=True)
class CalibrationShadowResult:
    status: str
    status_path: Path
    projection: ShadowProjection | None = None


@dataclass
class _IssueRecord:
    epoch_id: str
    target_date: str
    method: str
    station: str
    point_forecast_mm: float
    live_station_issue_entry_sha256: str
    issue: object
    issue_payload: dict[str, Any]
    state_before_sha256: str
    prospective_eligible: bool


@dataclass
class ShadowProjection:
    """Fully replayed scientific projection of the independent shadow ledger."""

    shadow_epoch_id: str
    upstream_live_epoch_id: str
    states: dict[str, dict[str, object]]
    epoch_open: bool
    activation_outstanding_target_date: str | None = None
    last_issue_batch_sha256: str = ZERO_HASH
    outstanding_target_date: str | None = None
    outstanding_issue_batch_sha256: str | None = None
    outstanding_live_seal_entry_sha256: str | None = None
    outstanding_prospective_eligible: bool = False
    outstanding_issues: dict[tuple[str, str], _IssueRecord] = field(
        default_factory=dict
    )
    issues_by_target: dict[str, dict[tuple[str, str], _IssueRecord]] = field(
        default_factory=dict
    )
    target_epoch: dict[str, str] = field(default_factory=dict)
    settled: dict[str, dict[str, Any]] = field(default_factory=dict)
    revisions: dict[str, dict[str, str]] = field(default_factory=dict)
    backfill_ineligible: dict[str, str] = field(default_factory=dict)
    eligible_daily: list[dict[str, Any]] = field(default_factory=list)
    sufficient_statistics: dict[str, dict[str, dict[str, float | int]]] = field(
        default_factory=dict
    )
    joint_available_target_dates: int = 0
    live_cursor_sequence_id: int = 0
    live_cursor_sha256: str = ZERO_HASH
    ledger_events: tuple[object, ...] = ()
    ledger_event_count: int = 0
    ledger_terminal_sequence_id: int = 0
    ledger_terminal_sha256: str = ZERO_HASH


ProjectionLoader = Callable[[dict[str, Any]], object | None]
Clock = Callable[[], datetime]


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CalibrationShadowConfig(f"cannot hash bound artifact: {path}") from exc
    return digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CalibrationShadowIntegrity("value is not canonical finite JSON") from exc


def _pretty_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CalibrationShadowIntegrity("status is not finite JSON") from exc


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _json_value(value: object) -> Any:
    """Round-trip a value through canonical JSON so tuples cannot drift on disk."""

    try:
        return json.loads(_canonical_bytes(value).decode("utf-8"))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise CalibrationShadowIntegrity(
            "value cannot be materialized from canonical JSON"
        ) from exc


def _require_sha256(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX_DIGITS for character in value)
    ):
        raise CalibrationShadowIntegrity(f"{name} must be a lowercase SHA-256")
    return value


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise CalibrationShadowIntegrity(f"{name} must be nonempty canonical text")
    return value


def _require_date(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise CalibrationShadowIntegrity(f"{name} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise CalibrationShadowIntegrity(f"{name} must be canonical YYYY-MM-DD")
    return text


def _finite(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationShadowIntegrity(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise CalibrationShadowIntegrity(f"{name} must be finite")
    return result


def _read_strict_json(path: Path, *, config: bool, name: str) -> tuple[dict, bytes]:
    error_type = CalibrationShadowConfig if config else CalibrationShadowIntegrity

    def reject_constant(value: str) -> None:
        raise ValueError(f"forbidden JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (
        OSError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise error_type(f"cannot read strict {name}: {path}") from exc
    if not isinstance(value, dict):
        raise error_type(f"{name} must be a JSON object")
    return value, raw


def _resolve(path: str | Path, *, base: Path) -> Path:
    candidate = Path(path)
    return (
        candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    )


def _confined(root: Path, relative: object, *, name: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise CalibrationShadowConfig(f"{name} must be a relative path")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise CalibrationShadowConfig(f"{name} escapes the shadow runtime root")
    resolved_root = root.resolve()
    cursor = resolved_root
    for part in candidate.parts:
        cursor /= part
        if cursor.is_symlink():
            raise CalibrationShadowConfig(f"{name} must not traverse a symlink")
    resolved = (resolved_root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise CalibrationShadowConfig(
            f"{name} escapes the shadow runtime root"
        ) from exc
    return resolved


def load_shadow_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load the exact reviewed shadow contract and verify every bound artifact."""

    profile, raw = _read_strict_json(path.resolve(), config=True, name="shadow profile")
    digest = _sha256_bytes(raw)
    if digest != DEFAULT_CONFIG_SHA256:
        raise CalibrationShadowConfig("shadow profile bytes differ from reviewed v1")
    required_top = {
        "schema_version",
        "profile_id",
        "profile_version",
        "case",
        "artifact_status",
        "default_pipeline_member",
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "selection_performed",
        "promotion_performed",
        "automatic_promotion_enabled",
        "real_activation_ready",
        "bound_artifacts",
        "runtime",
        "stations",
        "methods",
        "candidates",
        "protocol",
        "evaluation",
        "evidence",
    }
    if set(profile) != required_top:
        raise CalibrationShadowConfig("shadow profile keys changed")
    if (
        profile["schema_version"] != "ootang_prequential_calibration_shadow_profile_v1"
        or profile["profile_id"] != "ootang-prequential-calibration-shadow-v1"
        or profile["profile_version"] != "1.0.0-engineering"
        or profile["case"] != "ootang"
        or profile["artifact_status"]
        != "e2_calibration_shadow_engineering_only_not_live_evidence"
    ):
        raise CalibrationShadowConfig("shadow profile identity changed")
    for flag in (
        "default_pipeline_member",
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "selection_performed",
        "promotion_performed",
        "automatic_promotion_enabled",
        "real_activation_ready",
    ):
        if profile[flag] is not False:
            raise CalibrationShadowConfig(f"shadow flag {flag} must remain false")
    if tuple(profile["stations"]) != STATIONS or tuple(profile["methods"]) != METHODS:
        raise CalibrationShadowConfig("shadow method/station order changed")

    expected_bindings = {
        "live_profile",
        "deploy_profile",
        "bakeoff_profile",
        "calibration_core",
        "pyproject",
        "uv_lock",
    }
    bindings = profile["bound_artifacts"]
    if not isinstance(bindings, dict) or set(bindings) != expected_bindings:
        raise CalibrationShadowConfig("bound artifact registry changed")
    resolved_bindings: dict[str, str] = {}
    for name in sorted(expected_bindings):
        value = bindings[name]
        if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
            raise CalibrationShadowConfig(f"bound_artifacts.{name} changed")
        artifact_path = _resolve(value["path"], base=project_root)
        expected_sha = value["sha256"]
        if _sha256_file(artifact_path) != expected_sha:
            raise CalibrationShadowConfig(f"bound artifact changed: {name}")
        resolved_bindings[name] = str(artifact_path)

    bakeoff, _ = _read_strict_json(
        Path(resolved_bindings["bakeoff_profile"]),
        config=True,
        name="bound bakeoff profile",
    )
    expected_candidate = profile["candidates"]
    if (
        expected_candidate["agaci_ewa_variant_v1"]["gamma_grid"]
        != bakeoff["candidates"]["agaci_ewa_variant_v1"]["gamma_grid"]
        or expected_candidate["spci_qrf_v1"]["beta_grid"]
        != bakeoff["candidates"]["spci_qrf_v1"]["beta_grid"]
        or expected_candidate["spci_qrf_v1"]["random_state"]
        != bakeoff["candidates"]["spci_qrf_v1"]["random_state"]
    ):
        raise CalibrationShadowConfig("shadow candidate settings differ from bakeoff")
    evaluation = profile["evaluation"]
    expected_evaluation = {
        "target_coverage": 0.8,
        "joint_common_support_only": True,
        "minimum_joint_available_target_dates": 180,
        "minimum_station_dates_per_method": 180,
        "rolling_window_dates": 30,
        "coverage_absolute_gap_max": 0.05,
        "challenger_vs_aci_coverage_gap_margin": 0.02,
        "challenger_interval_score_ratio_max": 1.0,
        "availability_min": 0.95,
        "all_station_gate_required": True,
        "revision_backfill_ineligible_excluded": True,
        "gate_interpretation": ("engineering_readiness_only_no_automatic_promotion"),
    }
    if evaluation != expected_evaluation:
        raise CalibrationShadowConfig("predeclared evaluation contract changed")
    evidence = profile["evidence"]
    for false_flag in (
        "activation_outstanding_issue_is_prospective_eligible",
        "backfill_eligible",
        "revision_eligible",
        "runner_independent_replay_implemented",
        "trusted_anchor_receipt_verified",
        "e2_live_evidence_eligible",
    ):
        if evidence.get(false_flag) is not False:
            raise CalibrationShadowConfig(f"evidence.{false_flag} must remain false")
    if (
        evidence.get("prospective_support_requires_shadow_issue_before_live_settlement")
        is not True
        or evidence.get("promotion_requires_new_protocol_version") is not True
        or evidence.get("retrospective_promotion_prohibited") is not True
    ):
        raise CalibrationShadowConfig("shadow evidence boundary changed")

    enriched = dict(profile)
    enriched["_profile_path"] = str(path.resolve())
    enriched["_profile_sha256"] = digest
    enriched["_project_root"] = str(project_root.resolve())
    enriched["_bound_paths"] = resolved_bindings
    return enriched


def runtime_paths(
    profile: Mapping[str, Any],
    *,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
) -> ShadowRuntimePaths:
    configured = _resolve(profile["runtime"]["root"], base=project_root)
    root = configured if runtime_root is None else runtime_root.resolve()
    return ShadowRuntimePaths(
        root=root,
        ledger=_confined(root, profile["runtime"]["ledger"], name="runtime.ledger"),
        status=_confined(root, profile["runtime"]["status"], name="runtime.status"),
        lock=_confined(root, profile["runtime"]["lock"], name="runtime.lock"),
    )


def _core(profile: Mapping[str, Any]):
    from monitoring import calibration_challengers as challengers

    expected = profile["bound_artifacts"]["calibration_core"]["sha256"]
    if _sha256_file(Path(challengers.__file__).resolve()) != expected:
        raise CalibrationShadowIntegrity("calibration core changed after profile load")
    return challengers


def _spci_settings(profile: Mapping[str, Any], core: Any) -> object:
    value = profile["candidates"]["spci_qrf_v1"]
    return core.SpciSettings(
        beta_grid=tuple(value["beta_grid"]),
        lag=value["lag"],
        minimum_pairs=value["minimum_pairs"],
        n_estimators=value["n_estimators"],
        max_depth=value["max_depth"],
        min_samples_leaf=value["min_samples_leaf"],
        max_features=value["max_features"],
        bootstrap=value["bootstrap"],
        random_state=value["random_state"],
        n_jobs=value["n_jobs"],
    )


def _fresh_states(
    profile: Mapping[str, Any], core: Any
) -> dict[str, dict[str, object]]:
    gamma_grid = tuple(profile["candidates"]["agaci_ewa_variant_v1"]["gamma_grid"])
    spci = _spci_settings(profile, core)
    return {
        "aci_v1_control": {
            station: core.new_aci_state(reset_reason="initial_fold_start")
            for station in STATIONS
        },
        "agaci_ewa_variant_v1": {
            station: core.new_agaci_state(gamma_grid, reset_reason="initial_fold_start")
            for station in STATIONS
        },
        "spci_qrf_v1": {
            station: core.new_spci_state(spci, reset_reason="initial_fold_start")
            for station in STATIONS
        },
    }


def _fresh_state_after_drift(method: str, profile: Mapping[str, Any], core: Any):
    if method == "aci_v1_control":
        return core.new_aci_state(reset_reason="drift_detected_previous_date")
    if method == "agaci_ewa_variant_v1":
        return core.new_agaci_state(
            tuple(profile["candidates"][method]["gamma_grid"]),
            reset_reason="drift_detected_previous_date",
        )
    return core.new_spci_state(
        _spci_settings(profile, core),
        reset_reason="drift_detected_previous_date",
    )


def _state_payload(state: object, core: Any) -> dict[str, Any]:
    return json.loads(core.encode_state_canonical_json(state).decode("utf-8"))


def _state_hashes(states: Mapping[str, Mapping[str, object]], core: Any) -> dict:
    return {
        method: {
            station: core.state_sha256(states[method][station]) for station in STATIONS
        }
        for method in METHODS
    }


def _aggregate_state_sha(states: Mapping[str, Mapping[str, object]], core: Any) -> str:
    return _canonical_sha256(_state_hashes(states, core))


def _issue(
    method: str, state: object, point: float, profile: Mapping[str, Any], core: Any
):
    if method == "aci_v1_control":
        return core.issue_aci(state, point)
    if method == "agaci_ewa_variant_v1":
        return core.issue_agaci(state, point)
    return core.issue_spci(state, point, _spci_settings(profile, core))


def _reveal(method: str, state: object, issue: object, actual: float, core: Any):
    if method == "aci_v1_control":
        return core.reveal_aci(state, issue, actual)
    if method == "agaci_ewa_variant_v1":
        return core.reveal_agaci(state, issue, actual)
    return core.reveal_spci(state, issue, actual)


def _issue_payload(method: str, issue: object) -> dict[str, Any]:
    return _json_value({"algorithm": method, **asdict(issue)})


def _reveal_payload(method: str, reveal: object) -> dict[str, Any]:
    payload = asdict(reveal)
    payload.pop("updated_state")
    return _json_value({"algorithm": method, **payload})


def _interval_metrics(issue: object, actual: float) -> dict[str, Any]:
    lower = getattr(issue, "interval_lower_mm", None)
    upper = getattr(issue, "interval_upper_mm", None)
    if lower is None or upper is None:
        return {
            "interval_available": False,
            "interval_covered": None,
            "interval_width_mm": None,
            "interval_score_80_mm": None,
        }
    lower_value = float(lower)
    upper_value = float(upper)
    width = upper_value - lower_value
    score = width
    if actual < lower_value:
        score += 10.0 * (lower_value - actual)
    elif actual > upper_value:
        score += 10.0 * (actual - upper_value)
    return {
        "interval_available": True,
        "interval_covered": lower_value <= actual <= upper_value,
        "interval_width_mm": width,
        "interval_score_80_mm": score,
    }


def _new_statistics() -> dict[str, dict[str, dict[str, float | int]]]:
    return {
        method: {
            station: {
                "eligible_settled_count": 0,
                "interval_available_count": 0,
                "joint_available_count": 0,
                "joint_covered_count": 0,
                "joint_interval_score_sum": 0.0,
            }
            for station in STATIONS
        }
        for method in METHODS
    }


def _implementation_sha256(profile: Mapping[str, Any]) -> str:
    from monitoring import ootang_calibration_shadow_ledger as shadow_ledger

    paths = {
        "shadow_runner": Path(__file__).resolve(),
        "shadow_ledger": Path(shadow_ledger.__file__).resolve(),
        "calibration_core": Path(profile["_bound_paths"]["calibration_core"]),
        "profile": Path(profile["_profile_path"]),
        "pyproject": Path(profile["_bound_paths"]["pyproject"]),
        "uv_lock": Path(profile["_bound_paths"]["uv_lock"]),
    }
    return _canonical_sha256({name: _sha256_file(path) for name, path in paths.items()})


def _environment_sha256() -> str:
    try:
        import numpy
        import sklearn
    except ImportError as exc:
        raise CalibrationShadowIntegrity(
            "shadow numerical runtime is unavailable"
        ) from exc
    return _canonical_sha256(
        {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "numpy_version": numpy.__version__,
            "sklearn_version": sklearn.__version__,
        }
    )


def _exact_event_context(
    event: object, profile: Mapping[str, Any], code_sha: str, environment_sha: str
) -> None:
    if getattr(event, "protocol_config_sha256") != profile["_profile_sha256"]:
        raise CalibrationShadowIntegrity("shadow event profile binding changed")
    if getattr(event, "code_sha256") != code_sha:
        raise CalibrationShadowIntegrity("shadow event implementation binding changed")
    if getattr(event, "environment_sha256") != environment_sha:
        raise CalibrationShadowIntegrity("shadow event environment binding changed")


def _assert_issue_only(value: object, *, path: str = "issue") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(token in normalized for token in FORBIDDEN_ISSUE_KEY_TOKENS):
                raise CalibrationShadowIntegrity(
                    f"issue payload contains forbidden key at {path}.{key}"
                )
            _assert_issue_only(child, path=f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _assert_issue_only(child, path=f"{path}[{index}]")


def _expect_payload(actual: object, expected: object, *, name: str) -> None:
    if _canonical_bytes(actual) != _canonical_bytes(expected):
        raise CalibrationShadowIntegrity(f"{name} payload does not replay")


def _event_payload(event: object, *, name: str) -> dict[str, Any]:
    payload = getattr(event, "payload", None)
    if not isinstance(payload, Mapping):
        raise CalibrationShadowIntegrity(f"{name} payload must be an object")
    return _json_value(dict(payload))


def _event_hash(event: object, *, name: str) -> str:
    return _require_sha256(getattr(event, "entry_sha256", None), name=name)


def _event_input_sha(event: object, *, name: str) -> str:
    return _require_sha256(
        getattr(event, "input_manifest_sha256", None), name=f"{name}.input"
    )


def _event_model_sha(event: object, *, name: str) -> str:
    return _require_sha256(
        getattr(event, "model_manifest_sha256", None), name=f"{name}.model"
    )


def _live_cursor(live_projection: object) -> tuple[int, str]:
    events = tuple(getattr(live_projection, "ledger_events", ()))
    if not events:
        raise CalibrationShadowIntegrity(
            "verified live projection has no ledger events"
        )
    for expected, event in enumerate(events, start=1):
        sequence = getattr(event, "sequence_id", None)
        if sequence != expected:
            raise CalibrationShadowIntegrity("live event sequence is not contiguous")
        _event_hash(event, name=f"live_event[{expected}].entry")
        _event_payload(event, name=f"live_event[{expected}]")
    head = events[-1]
    count = getattr(live_projection, "ledger_event_count", len(events))
    terminal_sequence = getattr(
        live_projection, "ledger_terminal_sequence_id", head.sequence_id
    )
    terminal_hash = getattr(
        live_projection, "ledger_terminal_sha256", head.entry_sha256
    )
    if count != len(events) or terminal_sequence != head.sequence_id:
        raise CalibrationShadowIntegrity("live projection cursor count changed")
    if terminal_hash != head.entry_sha256:
        raise CalibrationShadowIntegrity("live projection terminal hash changed")
    epoch = _require_text(
        getattr(live_projection, "epoch_id", None), name="live.epoch_id"
    )
    del epoch
    return int(head.sequence_id), _event_hash(head, name="live.head")


def _source_cursor(event: object) -> tuple[int, str]:
    sequence = getattr(event, "sequence_id", None)
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise CalibrationShadowIntegrity("live source cursor sequence changed")
    return sequence, _event_hash(event, name="live.source.cursor")


def _source_cursor_with_floor(
    event: object, projection: ShadowProjection
) -> tuple[int, str]:
    cursor = _source_cursor(event)
    if cursor[0] < projection.live_cursor_sequence_id:
        return (
            projection.live_cursor_sequence_id,
            projection.live_cursor_sha256,
        )
    return cursor


def _live_event_map(live_projection: object) -> dict[str, object]:
    return {
        _event_hash(event, name="live.event.entry"): event
        for event in tuple(getattr(live_projection, "ledger_events", ()))
    }


def _events_of_type(live_projection: object, event_type: str) -> tuple[object, ...]:
    return tuple(
        event
        for event in tuple(getattr(live_projection, "ledger_events", ()))
        if getattr(event, "event_type", None) == event_type
    )


def _target_text(event: object, *, name: str) -> str:
    return _require_date(getattr(event, "target_date", None), name=name)


def _current_live_issue(
    live_projection: object,
) -> tuple[str, dict[str, object], object] | None:
    target = getattr(live_projection, "outstanding_target_date", None)
    if target is None:
        return None
    target_text = target.isoformat() if isinstance(target, date) else str(target)
    _require_date(target_text, name="live.outstanding_target_date")
    raw_events = getattr(live_projection, "issue_events", None)
    seal = getattr(live_projection, "seal_event", None)
    if not isinstance(raw_events, Mapping) or seal is None:
        raise CalibrationShadowIntegrity("live outstanding issue is not sealed")
    if tuple(raw_events) != STATIONS:
        raise CalibrationShadowIntegrity("live outstanding issue station order changed")
    issue_events = {station: raw_events[station] for station in STATIONS}
    if _target_text(seal, name="live.seal.target") != target_text:
        raise CalibrationShadowIntegrity("live outstanding seal target changed")
    for station in STATIONS:
        event = issue_events[station]
        if (
            getattr(event, "event_type", None) != "station_issue"
            or getattr(event, "station", None) != station
            or _target_text(event, name=f"live.issue.{station}.target") != target_text
        ):
            raise CalibrationShadowIntegrity("live outstanding station issue changed")
        point = _event_payload(event, name=f"live.issue.{station}").get("issue", {})
        if not isinstance(point, Mapping):
            raise CalibrationShadowIntegrity("live station issue body changed")
        _finite(point.get("point_forecast_mm"), name=f"live.point.{station}")
    return target_text, issue_events, seal


def _live_settlements(live_projection: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for event in _events_of_type(live_projection, "outcome_batch_settled"):
        target = _target_text(event, name="live.settlement.target")
        if target in result:
            raise CalibrationShadowIntegrity("live settlement target repeated")
        payload = _event_payload(event, name=f"live.settlement.{target}")
        actuals = payload.get("actual_by_station")
        if not isinstance(actuals, Mapping) or tuple(actuals) != STATIONS:
            raise CalibrationShadowIntegrity(
                "live settlement actual station order changed"
            )
        for station in STATIONS:
            _finite(actuals[station], name=f"live.settlement.{target}.{station}")
        _require_sha256(
            payload.get("issue_batch_sealed_entry_sha256"),
            name=f"live.settlement.{target}.seal",
        )
        _require_sha256(
            payload.get("outcome_batch_sha256"),
            name=f"live.settlement.{target}.batch",
        )
        _require_text(
            payload.get("source_revision_id"),
            name=f"live.settlement.{target}.revision",
        )
        result[target] = event
    return result


def _live_backfills(live_projection: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for event in _events_of_type(live_projection, "backfill_not_blind"):
        target = _target_text(event, name="live.backfill.target")
        if target in result:
            raise CalibrationShadowIntegrity("live backfill target repeated")
        result[target] = event
    return result


def _drift_events_for_settlement(
    live_projection: object, settlement: object
) -> dict[str, object]:
    transaction = _require_sha256(
        getattr(settlement, "transaction_sha256", None),
        name="live.settlement.transaction",
    )
    rows = [
        event
        for event in tuple(getattr(live_projection, "ledger_events", ()))
        if getattr(event, "event_type", None) == "drift_state_updated"
        and getattr(event, "transaction_sha256", None) == transaction
    ]
    if tuple(getattr(event, "station", None) for event in rows) != STATIONS:
        raise CalibrationShadowIntegrity(
            "live settlement does not contain eight ordered drift decisions"
        )
    return {str(event.station): event for event in rows}


def _live_revision_groups(
    live_projection: object,
) -> dict[tuple[str, str], dict[str, object]]:
    groups: dict[tuple[str, str], dict[str, object]] = {}
    metadata: dict[tuple[str, str], tuple[str, str]] = {}
    for event in _events_of_type(live_projection, "outcome_revision"):
        target = _target_text(event, name="live.revision.target")
        station = getattr(event, "station", None)
        if station not in STATIONS:
            raise CalibrationShadowIntegrity("live revision station changed")
        payload = _event_payload(event, name=f"live.revision.{target}.{station}")
        revision = _require_text(
            payload.get("source_revision_id"), name="live.revision.id"
        )
        batch_sha = _require_sha256(
            payload.get("outcome_batch_sha256"), name="live.revision.batch"
        )
        transaction = _require_sha256(
            getattr(event, "transaction_sha256", None),
            name="live.revision.transaction",
        )
        key = (target, revision)
        expected = metadata.setdefault(key, (batch_sha, transaction))
        if expected != (batch_sha, transaction):
            raise CalibrationShadowIntegrity("live revision group provenance changed")
        rows = groups.setdefault(key, {})
        if station in rows:
            raise CalibrationShadowIntegrity("live revision repeats a station")
        _finite(payload.get("revised_actual_mm"), name="live.revision.actual")
        rows[str(station)] = event
    for key, rows in groups.items():
        if tuple(rows) != STATIONS:
            raise CalibrationShadowIntegrity(
                f"live revision {key!r} is not complete for all stations"
            )
    return groups


def _spec(
    *,
    profile: Mapping[str, Any],
    code_sha: str,
    environment_sha: str,
    source_event: object,
    event_key: str,
    event_type: str,
    payload: Mapping[str, Any],
    target_date: str | None = None,
    station: str | None = None,
    issue_id: str | None = None,
    state_before_sha256: str = ZERO_HASH,
    state_after_sha256: str = ZERO_HASH,
):
    from monitoring.ootang_calibration_shadow_ledger import EventSpec

    return EventSpec(
        event_key=event_key,
        event_type=event_type,
        target_date=target_date,
        station=station,
        issue_id=issue_id,
        protocol_config_sha256=profile["_profile_sha256"],
        code_sha256=code_sha,
        environment_sha256=environment_sha,
        input_manifest_sha256=_event_input_sha(source_event, name=event_key),
        model_manifest_sha256=_event_model_sha(source_event, name=event_key),
        state_before_sha256=state_before_sha256,
        state_after_sha256=state_after_sha256,
        payload=_json_value(dict(payload)),
    )


def _acquire_machine_lock(path: Path, *, label: str):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise CalibrationShadowBusy(f"{label} machine lock is already held") from exc
    except OSError as exc:
        try:
            handle.close()
        except (NameError, OSError):
            pass
        raise CalibrationShadowIntegrity(
            f"{label} machine lock cannot be acquired"
        ) from exc
    return handle


def _release_machine_lock(handle: object) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    except OSError as exc:
        raise CalibrationShadowIntegrity("machine lock cannot be released") from exc


def _release_locks(handles: Sequence[object | None]) -> None:
    active_exception = sys.exc_info()[0] is not None
    release_error: CalibrationShadowIntegrity | None = None
    for handle in handles:
        if handle is None:
            continue
        try:
            _release_machine_lock(handle)
        except CalibrationShadowIntegrity as exc:
            if release_error is None:
                release_error = exc
    if release_error is not None and not active_exception:
        raise release_error


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = _pretty_bytes(dict(payload))
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
    except OSError as exc:
        raise CalibrationShadowIntegrity("status staging cannot be created") from exc
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise CalibrationShadowIntegrity("status cannot be durably replaced") from exc
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _clock_text(clock: Clock | None) -> str:
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CalibrationShadowConfig("clock must return an aware datetime")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _candidate_issue_payload(
    *,
    projection: ShadowProjection,
    method: str,
    station: str,
    point: float,
    live_issue_event: object,
    issue: object,
    prospective_eligible: bool,
    live_cursor: tuple[int, str],
) -> dict[str, Any]:
    state_hash = issue.state_before_sha256
    payload = {
        "schema_version": "shadow_candidate_issue_v1",
        "shadow_epoch_id": projection.shadow_epoch_id,
        "target_date": _target_text(live_issue_event, name="live.issue.target"),
        "method": method,
        "station": station,
        "point_forecast_mm": point,
        "live_station_issue_entry_sha256": _event_hash(
            live_issue_event, name="live.station_issue"
        ),
        "state_before_sha256": state_hash,
        "engineering_prospective_ordering_candidate": prospective_eligible,
        "live_cursor_sequence_id": live_cursor[0],
        "live_cursor_sha256": live_cursor[1],
        "issue": _issue_payload(method, issue),
        "selection_performed": False,
        "promotion_performed": False,
        "formal_warning_output": False,
    }
    _assert_issue_only(payload)
    return payload


def _issue_batch_scientific_sha(
    *,
    epoch_id: str,
    target: str,
    previous_issue_batch_sha256: str,
    live_seal_sha256: str,
    prospective_eligible: bool,
    candidate_payloads: Sequence[Mapping[str, Any]],
) -> str:
    return _canonical_sha256(
        {
            "schema_version": "shadow_issue_batch_digest_v1",
            "shadow_epoch_id": epoch_id,
            "target_date": target,
            "previous_issue_batch_sha256": previous_issue_batch_sha256,
            "live_issue_seal_entry_sha256": live_seal_sha256,
            "engineering_prospective_ordering_candidate": prospective_eligible,
            "candidate_issues": list(candidate_payloads),
        }
    )


def _metrics_for_all(
    issues: Mapping[tuple[str, str], _IssueRecord], actuals: Mapping[str, Any]
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, bool], bool]:
    metrics: dict[str, dict[str, dict[str, Any]]] = {}
    station_joint: dict[str, bool] = {}
    for method in METHODS:
        metrics[method] = {}
        for station in STATIONS:
            actual = _finite(actuals[station], name=f"actual.{station}")
            metrics[method][station] = _interval_metrics(
                issues[(method, station)].issue, actual
            )
    for station in STATIONS:
        station_joint[station] = all(
            metrics[method][station]["interval_available"] for method in METHODS
        )
    return metrics, station_joint, all(station_joint.values())


def _update_statistics(
    projection: ShadowProjection,
    metrics: Mapping[str, Mapping[str, Mapping[str, Any]]],
    station_joint: Mapping[str, bool],
    *,
    prospective_eligible: bool,
) -> None:
    if not prospective_eligible:
        return
    if all(station_joint.values()):
        projection.joint_available_target_dates += 1
    for method in METHODS:
        for station in STATIONS:
            row = projection.sufficient_statistics[method][station]
            row["eligible_settled_count"] += 1
            metric = metrics[method][station]
            if metric["interval_available"]:
                row["interval_available_count"] += 1
            if station_joint[station]:
                row["joint_available_count"] += 1
                if metric["interval_covered"]:
                    row["joint_covered_count"] += 1
                row["joint_interval_score_sum"] += float(metric["interval_score_80_mm"])


def _genesis_identity(
    profile: Mapping[str, Any],
    live_projection: object,
    previous_terminal_sha256: str,
) -> str:
    cursor = _live_cursor(live_projection)
    return _canonical_sha256(
        {
            "schema_version": "shadow_epoch_identity_v1",
            "profile_sha256": profile["_profile_sha256"],
            "upstream_live_epoch_id": str(live_projection.epoch_id),
            "activation_live_cursor_sequence_id": cursor[0],
            "activation_live_cursor_sha256": cursor[1],
            "previous_shadow_epoch_terminal_sha256": previous_terminal_sha256,
        }
    )


def _append_genesis(
    ledger: object,
    profile: Mapping[str, Any],
    live_projection: object,
    *,
    code_sha: str,
    environment_sha: str,
    previous_terminal_sha256: str,
) -> None:
    core = _core(profile)
    states = _fresh_states(profile, core)
    cursor = _live_cursor(live_projection)
    current_issue = _current_live_issue(live_projection)
    activation_target = None if current_issue is None else current_issue[0]
    epoch_id = _genesis_identity(profile, live_projection, previous_terminal_sha256)
    aggregate = _aggregate_state_sha(states, core)
    payload = {
        "schema_version": "shadow_epoch_genesis_v1",
        "shadow_epoch_id": epoch_id,
        "upstream_live_epoch_id": str(live_projection.epoch_id),
        "activation_live_cursor_sequence_id": cursor[0],
        "activation_live_cursor_sha256": cursor[1],
        "activation_outstanding_target_date": activation_target,
        "activation_outstanding_prospective_eligible": False,
        "cold_start_semantic_reason": profile["protocol"]["cold_start_semantic_reason"],
        "core_reset_token": profile["protocol"]["cold_start_core_reset_token"],
        "candidate_order": list(METHODS),
        "station_order": list(STATIONS),
        "candidate_settings": profile["candidates"],
        "candidate_settings_sha256": _canonical_sha256(profile["candidates"]),
        "evaluation_contract": profile["evaluation"],
        "evaluation_contract_sha256": _canonical_sha256(profile["evaluation"]),
        "initial_state_sha256": _state_hashes(states, core),
        "initial_state_aggregate_sha256": aggregate,
        "previous_shadow_epoch_terminal_sha256": previous_terminal_sha256,
        "full_replay_required": True,
        "support_metrics_replayable": True,
        "selection_performed": False,
        "promotion_performed": False,
        "automatic_promotion_enabled": False,
        "formal_warning_output": False,
        "e2_live_evidence_eligible": False,
    }
    source = tuple(live_projection.ledger_events)[-1]
    ledger.append_transaction(
        [
            _spec(
                profile=profile,
                code_sha=code_sha,
                environment_sha=environment_sha,
                source_event=source,
                event_key=f"{epoch_id}:genesis",
                event_type="shadow_epoch_genesis",
                payload=payload,
                state_before_sha256=aggregate,
                state_after_sha256=aggregate,
            )
        ]
    )


def _append_issue_batch(
    ledger: object,
    profile: Mapping[str, Any],
    projection: ShadowProjection,
    live_projection: object,
    *,
    code_sha: str,
    environment_sha: str,
) -> None:
    live_issue = _current_live_issue(live_projection)
    if live_issue is None or projection.outstanding_target_date is not None:
        raise CalibrationShadowIntegrity("shadow issue append lifecycle changed")
    target, live_station_events, live_seal = live_issue
    live_seal_sha = _event_hash(live_seal, name="live.issue.seal")
    cursor = _source_cursor_with_floor(live_seal, projection)
    prospective = target != projection.activation_outstanding_target_date
    core = _core(profile)
    aggregate = _aggregate_state_sha(projection.states, core)
    candidate_payloads: list[dict[str, Any]] = []
    candidates: list[tuple[str, str, object, dict[str, Any]]] = []
    for method, station in PAIR_ORDER:
        live_event = live_station_events[station]
        live_issue_payload = _event_payload(
            live_event, name=f"live.issue.{target}.{station}"
        ).get("issue")
        if not isinstance(live_issue_payload, Mapping):
            raise CalibrationShadowIntegrity("live issue body is not an object")
        point = _finite(
            live_issue_payload.get("point_forecast_mm"),
            name=f"live.issue.{target}.{station}.point",
        )
        issue = _issue(method, projection.states[method][station], point, profile, core)
        payload = _candidate_issue_payload(
            projection=projection,
            method=method,
            station=station,
            point=point,
            live_issue_event=live_event,
            issue=issue,
            prospective_eligible=prospective,
            live_cursor=cursor,
        )
        candidate_payloads.append(payload)
        candidates.append((method, station, issue, payload))
    issue_batch_sha = _issue_batch_scientific_sha(
        epoch_id=projection.shadow_epoch_id,
        target=target,
        previous_issue_batch_sha256=projection.last_issue_batch_sha256,
        live_seal_sha256=live_seal_sha,
        prospective_eligible=prospective,
        candidate_payloads=candidate_payloads,
    )
    prefix = f"{projection.shadow_epoch_id}:{target}:issue"
    opened_payload = {
        "schema_version": "shadow_issue_batch_opened_v1",
        "shadow_epoch_id": projection.shadow_epoch_id,
        "target_date": target,
        "live_issue_seal_entry_sha256": live_seal_sha,
        "previous_issue_batch_sha256": projection.last_issue_batch_sha256,
        "engineering_prospective_ordering_candidate": prospective,
        "candidate_count": len(PAIR_ORDER),
        "all_candidate_issues_prepared_before_append": True,
        "live_cursor_sequence_id": cursor[0],
        "live_cursor_sha256": cursor[1],
        "selection_performed": False,
        "promotion_performed": False,
        "formal_warning_output": False,
    }
    _assert_issue_only(opened_payload)
    specs = [
        _spec(
            profile=profile,
            code_sha=code_sha,
            environment_sha=environment_sha,
            source_event=live_seal,
            event_key=f"{prefix}:opened",
            event_type="shadow_issue_batch_opened",
            payload=opened_payload,
            target_date=target,
            issue_id=issue_batch_sha,
            state_before_sha256=aggregate,
            state_after_sha256=aggregate,
        )
    ]
    for method, station, issue, payload in candidates:
        state_sha = core.state_sha256(projection.states[method][station])
        specs.append(
            _spec(
                profile=profile,
                code_sha=code_sha,
                environment_sha=environment_sha,
                source_event=live_station_events[station],
                event_key=f"{prefix}:candidate:{method}:{station}",
                event_type="shadow_candidate_issued",
                payload=payload,
                target_date=target,
                station=station,
                issue_id=issue_batch_sha,
                state_before_sha256=state_sha,
                state_after_sha256=state_sha,
            )
        )
    sealed_payload = {
        "schema_version": "shadow_issue_batch_sealed_v1",
        "shadow_epoch_id": projection.shadow_epoch_id,
        "target_date": target,
        "live_issue_seal_entry_sha256": live_seal_sha,
        "previous_issue_batch_sha256": projection.last_issue_batch_sha256,
        "issue_batch_sha256": issue_batch_sha,
        "engineering_prospective_ordering_candidate": prospective,
        "candidate_event_keys": [spec.event_key for spec in specs[1:]],
        "candidate_count": len(PAIR_ORDER),
        "same_date_information_excluded": True,
        "live_cursor_sequence_id": cursor[0],
        "live_cursor_sha256": cursor[1],
        "selection_performed": False,
        "promotion_performed": False,
        "formal_warning_output": False,
    }
    _assert_issue_only(sealed_payload)
    specs.append(
        _spec(
            profile=profile,
            code_sha=code_sha,
            environment_sha=environment_sha,
            source_event=live_seal,
            event_key=f"{prefix}:sealed",
            event_type="shadow_issue_batch_sealed",
            payload=sealed_payload,
            target_date=target,
            issue_id=issue_batch_sha,
            state_before_sha256=aggregate,
            state_after_sha256=aggregate,
        )
    )
    ledger.append_transaction(specs)


def _append_outcome_batch(
    ledger: object,
    profile: Mapping[str, Any],
    projection: ShadowProjection,
    live_projection: object,
    settlement: object,
    *,
    code_sha: str,
    environment_sha: str,
) -> None:
    target = _target_text(settlement, name="live.settlement.target")
    if target != projection.outstanding_target_date:
        raise CalibrationShadowIntegrity("shadow settlement target changed")
    live_payload = _event_payload(settlement, name=f"live.settlement.{target}")
    live_seal_sha = _require_sha256(
        live_payload.get("issue_batch_sealed_entry_sha256"),
        name="live.settlement.seal",
    )
    if live_seal_sha != projection.outstanding_live_seal_entry_sha256:
        raise CalibrationShadowIntegrity(
            "live settlement seal differs from shadow issue"
        )
    actuals = live_payload["actual_by_station"]
    revision_id = _require_text(
        live_payload.get("source_revision_id"), name="live.settlement.revision"
    )
    live_batch_sha = _require_sha256(
        live_payload.get("outcome_batch_sha256"), name="live.settlement.batch"
    )
    drift_events = _drift_events_for_settlement(live_projection, settlement)
    cursor = _source_cursor(settlement)
    core = _core(profile)
    initial_aggregate = _aggregate_state_sha(projection.states, core)
    reveals: dict[tuple[str, str], object] = {}
    next_states = {method: dict(projection.states[method]) for method in METHODS}
    reveal_payloads: list[dict[str, Any]] = []
    update_payloads: list[dict[str, Any]] = []
    for method, station in PAIR_ORDER:
        record = projection.outstanding_issues[(method, station)]
        actual = _finite(actuals[station], name=f"actual.{station}")
        reveal = _reveal(
            method,
            projection.states[method][station],
            record.issue,
            actual,
            core,
        )
        reveals[(method, station)] = reveal
        drift_payload = _event_payload(
            drift_events[station], name=f"live.drift.{target}.{station}"
        )
        drift_detected = drift_payload.get("drift_detected")
        if not isinstance(drift_detected, bool):
            raise CalibrationShadowIntegrity("live drift decision is not boolean")
        updated_state = reveal.updated_state
        effective_state = (
            _fresh_state_after_drift(method, profile, core)
            if drift_detected
            else updated_state
        )
        next_states[method][station] = effective_state
        reveal_payloads.append(
            {
                "schema_version": "shadow_candidate_reveal_v1",
                "shadow_epoch_id": projection.shadow_epoch_id,
                "target_date": target,
                "method": method,
                "station": station,
                "actual_mm": actual,
                "source_revision_id": revision_id,
                "live_settlement_entry_sha256": _event_hash(
                    settlement, name="live.settlement"
                ),
                "live_issue_seal_entry_sha256": live_seal_sha,
                "shadow_issue_batch_sha256": (
                    projection.outstanding_issue_batch_sha256
                ),
                "shadow_issue_state_sha256": record.state_before_sha256,
                "engineering_prospective_ordering_candidate": (
                    projection.outstanding_prospective_eligible
                ),
                "reveal": _reveal_payload(method, reveal),
                "metrics": _interval_metrics(record.issue, actual),
                "live_cursor_sequence_id": cursor[0],
                "live_cursor_sha256": cursor[1],
                "promotion_performed": False,
                "formal_warning_output": False,
            }
        )
        update_payloads.append(
            {
                "schema_version": "shadow_state_update_v1",
                "shadow_epoch_id": projection.shadow_epoch_id,
                "target_date": target,
                "method": method,
                "station": station,
                "source_revision_id": revision_id,
                "live_drift_event_entry_sha256": _event_hash(
                    drift_events[station], name="live.drift"
                ),
                "live_drift_detected": drift_detected,
                "updated_state_sha256": core.state_sha256(updated_state),
                "effective_next_state_sha256": core.state_sha256(effective_state),
                "effective_next_state": _state_payload(effective_state, core),
                "reset_reason": effective_state.reset_reason,
                "reset_applies_next_target_date": True,
                "live_online_state_affected": False,
                "live_cursor_sequence_id": cursor[0],
                "live_cursor_sha256": cursor[1],
                "promotion_performed": False,
                "formal_warning_output": False,
            }
        )
    metrics, station_joint, joint_all = _metrics_for_all(
        projection.outstanding_issues, actuals
    )
    final_aggregate = _aggregate_state_sha(next_states, core)
    shadow_outcome_sha = _canonical_sha256(
        {
            "schema_version": "shadow_outcome_batch_digest_v1",
            "shadow_epoch_id": projection.shadow_epoch_id,
            "target_date": target,
            "live_settlement_entry_sha256": _event_hash(
                settlement, name="live.settlement"
            ),
            "live_outcome_batch_sha256": live_batch_sha,
            "shadow_issue_batch_sha256": projection.outstanding_issue_batch_sha256,
            "reveals": reveal_payloads,
            "state_updates": update_payloads,
        }
    )
    revision_token = _sha256_bytes(revision_id.encode("utf-8"))
    prefix = f"{projection.shadow_epoch_id}:{target}:outcome:{revision_token}"
    opened_payload = {
        "schema_version": "shadow_outcome_batch_opened_v1",
        "shadow_epoch_id": projection.shadow_epoch_id,
        "target_date": target,
        "source_revision_id": revision_id,
        "live_outcome_batch_sha256": live_batch_sha,
        "live_settlement_entry_sha256": _event_hash(settlement, name="live.settlement"),
        "live_issue_seal_entry_sha256": live_seal_sha,
        "shadow_issue_batch_sha256": projection.outstanding_issue_batch_sha256,
        "all_eight_actuals_loaded_after_shadow_seal": True,
        "engineering_prospective_ordering_candidate": (
            projection.outstanding_prospective_eligible
        ),
        "live_cursor_sequence_id": cursor[0],
        "live_cursor_sha256": cursor[1],
        "promotion_performed": False,
        "formal_warning_output": False,
    }
    specs = [
        _spec(
            profile=profile,
            code_sha=code_sha,
            environment_sha=environment_sha,
            source_event=settlement,
            event_key=f"{prefix}:opened",
            event_type="shadow_outcome_batch_opened",
            payload=opened_payload,
            target_date=target,
            issue_id=projection.outstanding_issue_batch_sha256,
            state_before_sha256=initial_aggregate,
            state_after_sha256=initial_aggregate,
        )
    ]
    for (method, station), payload in zip(PAIR_ORDER, reveal_payloads, strict=True):
        state_sha = core.state_sha256(projection.states[method][station])
        specs.append(
            _spec(
                profile=profile,
                code_sha=code_sha,
                environment_sha=environment_sha,
                source_event=settlement,
                event_key=f"{prefix}:candidate:{method}:{station}",
                event_type="shadow_candidate_revealed",
                payload=payload,
                target_date=target,
                station=station,
                issue_id=projection.outstanding_issue_batch_sha256,
                state_before_sha256=state_sha,
                state_after_sha256=state_sha,
            )
        )
    for (method, station), payload in zip(PAIR_ORDER, update_payloads, strict=True):
        before = core.state_sha256(projection.states[method][station])
        after = core.state_sha256(next_states[method][station])
        specs.append(
            _spec(
                profile=profile,
                code_sha=code_sha,
                environment_sha=environment_sha,
                source_event=drift_events[station],
                event_key=f"{prefix}:state:{method}:{station}",
                event_type="shadow_state_updated",
                payload=payload,
                target_date=target,
                station=station,
                issue_id=projection.outstanding_issue_batch_sha256,
                state_before_sha256=before,
                state_after_sha256=after,
            )
        )
    settled_payload = {
        "schema_version": "shadow_outcome_batch_settled_v1",
        "shadow_epoch_id": projection.shadow_epoch_id,
        "target_date": target,
        "source_revision_id": revision_id,
        "live_outcome_batch_sha256": live_batch_sha,
        "live_settlement_entry_sha256": _event_hash(settlement, name="live.settlement"),
        "live_issue_seal_entry_sha256": live_seal_sha,
        "shadow_issue_batch_sha256": projection.outstanding_issue_batch_sha256,
        "shadow_outcome_batch_sha256": shadow_outcome_sha,
        "actual_by_station": dict(actuals),
        "engineering_prospective_ordering_candidate": (
            projection.outstanding_prospective_eligible
        ),
        "metrics": metrics,
        "joint_available_by_station": station_joint,
        "all_station_joint_available": joint_all,
        "revision_backfill_ineligible_excluded": True,
        "support_metrics_replayable": True,
        "live_cursor_sequence_id": cursor[0],
        "live_cursor_sha256": cursor[1],
        "selection_performed": False,
        "promotion_performed": False,
        "automatic_promotion_enabled": False,
        "formal_warning_output": False,
    }
    specs.append(
        _spec(
            profile=profile,
            code_sha=code_sha,
            environment_sha=environment_sha,
            source_event=settlement,
            event_key=f"{prefix}:settled",
            event_type="shadow_outcome_batch_settled",
            payload=settled_payload,
            target_date=target,
            issue_id=projection.outstanding_issue_batch_sha256,
            state_before_sha256=initial_aggregate,
            state_after_sha256=final_aggregate,
        )
    )
    ledger.append_transaction(specs)


def _append_backfill_ineligible(
    ledger: object,
    profile: Mapping[str, Any],
    projection: ShadowProjection,
    live_projection: object,
    source: object,
    *,
    code_sha: str,
    environment_sha: str,
) -> None:
    target = _target_text(source, name="live.backfill_source.target")
    payload = _event_payload(source, name=f"live.backfill_source.{target}")
    event_type = getattr(source, "event_type", None)
    if event_type == "outcome_batch_settled":
        reason = "live_settlement_preceded_durable_shadow_issue_seal"
        source_kind = "settlement_without_shadow_issue"
        live_batch_sha = _require_sha256(
            payload.get("outcome_batch_sha256"), name="live.backfill.batch"
        )
        source_revision = _require_text(
            payload.get("source_revision_id"), name="live.backfill.revision"
        )
        live_seal = _require_sha256(
            payload.get("issue_batch_sealed_entry_sha256"),
            name="live.backfill.seal",
        )
    elif event_type == "backfill_not_blind":
        reason = "live_classified_target_as_backfill_before_shadow_issue"
        source_kind = "upstream_backfill_not_blind"
        live_batch_sha = _require_sha256(
            payload.get("outcome_batch_sha256"), name="live.backfill.batch"
        )
        source_revision = _require_text(
            payload.get("source_revision_id"), name="live.backfill.revision"
        )
        live_seal = None
    else:
        raise CalibrationShadowIntegrity("unsupported live backfill source")
    core = _core(profile)
    aggregate = _aggregate_state_sha(projection.states, core)
    cursor = _source_cursor(source)
    live_entry = _event_hash(source, name="live.backfill_source")
    record = {
        "schema_version": "shadow_backfill_ineligible_v1",
        "shadow_epoch_id": projection.shadow_epoch_id,
        "target_date": target,
        "reason": reason,
        "source_kind": source_kind,
        "live_source_entry_sha256": live_entry,
        "live_outcome_batch_sha256": live_batch_sha,
        "source_revision_id": source_revision,
        "live_issue_seal_entry_sha256": live_seal,
        "shadow_issue_created": False,
        "shadow_state_updated": False,
        "eligible_metrics_updated": False,
        "retrospective_promotion_prohibited": True,
        "live_cursor_sequence_id": cursor[0],
        "live_cursor_sha256": cursor[1],
        "selection_performed": False,
        "promotion_performed": False,
        "formal_warning_output": False,
    }
    ledger.append_transaction(
        [
            _spec(
                profile=profile,
                code_sha=code_sha,
                environment_sha=environment_sha,
                source_event=source,
                event_key=(
                    f"{projection.shadow_epoch_id}:{target}:backfill_ineligible"
                ),
                event_type="shadow_backfill_ineligible",
                payload=record,
                target_date=target,
                state_before_sha256=aggregate,
                state_after_sha256=aggregate,
            )
        ]
    )


def _append_revision_rescores(
    ledger: object,
    profile: Mapping[str, Any],
    projection: ShadowProjection,
    live_projection: object,
    *,
    target: str,
    revision_id: str,
    live_rows: Mapping[str, object],
    code_sha: str,
    environment_sha: str,
) -> None:
    issues = projection.issues_by_target.get(target)
    settled = projection.settled.get(target)
    if issues is None or settled is None:
        raise CalibrationShadowIntegrity("revision has no original shadow issue")
    cursor = _source_cursor(
        max(live_rows.values(), key=lambda event: int(event.sequence_id))
    )
    core = _core(profile)
    first_payload = _event_payload(live_rows[STATIONS[0]], name="live.revision.first")
    live_batch_sha = _require_sha256(
        first_payload.get("outcome_batch_sha256"), name="live.revision.batch"
    )
    specs = []
    revision_token = _sha256_bytes(revision_id.encode("utf-8"))
    prefix = f"{projection.target_epoch[target]}:{target}:revision:{revision_token}"
    for method, station in PAIR_ORDER:
        source = live_rows[station]
        source_payload = _event_payload(
            source, name=f"live.revision.{target}.{station}"
        )
        if (
            source_payload.get("source_revision_id") != revision_id
            or source_payload.get("outcome_batch_sha256") != live_batch_sha
        ):
            raise CalibrationShadowIntegrity("live revision group content changed")
        actual = _finite(
            source_payload.get("revised_actual_mm"),
            name=f"live.revision.{target}.{station}.actual",
        )
        issue = issues[(method, station)]
        current_state_sha = core.state_sha256(projection.states[method][station])
        payload = {
            "schema_version": "shadow_outcome_revision_rescore_v1",
            "shadow_epoch_id": projection.target_epoch[target],
            "target_date": target,
            "method": method,
            "station": station,
            "source_revision_id": revision_id,
            "live_outcome_batch_sha256": live_batch_sha,
            "live_revision_entry_sha256": _event_hash(source, name="live.revision"),
            "original_shadow_issue_batch_sha256": settled["shadow_issue_batch_sha256"],
            "original_shadow_outcome_batch_sha256": settled[
                "shadow_outcome_batch_sha256"
            ],
            "revised_actual_mm": actual,
            "metrics": _interval_metrics(issue.issue, actual),
            "state_before_sha256": current_state_sha,
            "state_after_sha256": current_state_sha,
            "online_state_updated": False,
            "eligible_metrics_updated": False,
            "retrospective_view_only": True,
            "retrospective_promotion_prohibited": True,
            "live_cursor_sequence_id": cursor[0],
            "live_cursor_sha256": cursor[1],
            "selection_performed": False,
            "promotion_performed": False,
            "formal_warning_output": False,
        }
        specs.append(
            _spec(
                profile=profile,
                code_sha=code_sha,
                environment_sha=environment_sha,
                source_event=source,
                event_key=f"{prefix}:{method}:{station}",
                event_type="shadow_outcome_revision_rescored",
                payload=payload,
                target_date=target,
                station=station,
                issue_id=settled["shadow_issue_batch_sha256"],
                state_before_sha256=current_state_sha,
                state_after_sha256=current_state_sha,
            )
        )
    ledger.append_transaction(specs)


def _append_epoch_close(
    ledger: object,
    profile: Mapping[str, Any],
    projection: ShadowProjection,
    live_projection: object,
    *,
    code_sha: str,
    environment_sha: str,
) -> None:
    if projection.outstanding_target_date is not None:
        raise CalibrationShadowIntegrity(
            "upstream live epoch changed with an outstanding shadow issue"
        )
    cursor = _live_cursor(live_projection)
    core = _core(profile)
    aggregate = _aggregate_state_sha(projection.states, core)
    source = tuple(live_projection.ledger_events)[-1]
    payload = {
        "schema_version": "shadow_epoch_closed_v1",
        "shadow_epoch_id": projection.shadow_epoch_id,
        "upstream_live_epoch_id": projection.upstream_live_epoch_id,
        "next_upstream_live_epoch_id": str(live_projection.epoch_id),
        "reason": "verified_upstream_live_epoch_changed",
        "terminal_state_sha256": _state_hashes(projection.states, core),
        "terminal_state_aggregate_sha256": aggregate,
        "live_cursor_sequence_id": cursor[0],
        "live_cursor_sha256": cursor[1],
        "selection_performed": False,
        "promotion_performed": False,
        "formal_warning_output": False,
    }
    ledger.append_transaction(
        [
            _spec(
                profile=profile,
                code_sha=code_sha,
                environment_sha=environment_sha,
                source_event=source,
                event_key=(
                    f"{projection.shadow_epoch_id}:close:"
                    f"{str(live_projection.epoch_id)}"
                ),
                event_type="shadow_epoch_closed",
                payload=payload,
                state_before_sha256=aggregate,
                state_after_sha256=aggregate,
            )
        ]
    )


def _validate_event_envelope(
    event: object,
    *,
    event_key: str,
    event_type: str,
    target_date: str | None,
    station: str | None,
    issue_id: str | None,
    state_before_sha256: str,
    state_after_sha256: str,
) -> None:
    expected = {
        "event_key": event_key,
        "event_type": event_type,
        "target_date": target_date,
        "station": station,
        "issue_id": issue_id,
        "state_before_sha256": state_before_sha256,
        "state_after_sha256": state_after_sha256,
    }
    for name, value in expected.items():
        if getattr(event, name, None) != value:
            raise CalibrationShadowIntegrity(
                f"shadow event {getattr(event, 'sequence_id', '?')} {name} changed"
            )


def _payload_cursor(payload: Mapping[str, Any], *, name: str) -> tuple[int, str]:
    sequence = payload.get("live_cursor_sequence_id")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise CalibrationShadowIntegrity(f"{name} live cursor sequence changed")
    digest = _require_sha256(
        payload.get("live_cursor_sha256"), name=f"{name}.live_cursor"
    )
    return sequence, digest


def _advance_projection_cursor(
    projection: ShadowProjection, cursor: tuple[int, str]
) -> None:
    if cursor[0] < projection.live_cursor_sequence_id:
        raise CalibrationShadowIntegrity("shadow live cursor moved backwards")
    if (
        cursor[0] == projection.live_cursor_sequence_id
        and projection.live_cursor_sequence_id > 0
        and cursor[1] != projection.live_cursor_sha256
    ):
        raise CalibrationShadowIntegrity("shadow live cursor hash forked")
    projection.live_cursor_sequence_id = cursor[0]
    projection.live_cursor_sha256 = cursor[1]


def _preserved_history(projection: ShadowProjection | None) -> dict[str, Any]:
    if projection is None:
        return {
            "issues_by_target": {},
            "target_epoch": {},
            "settled": {},
            "revisions": {},
            "backfill_ineligible": {},
        }
    return {
        "issues_by_target": projection.issues_by_target,
        "target_epoch": projection.target_epoch,
        "settled": projection.settled,
        "revisions": projection.revisions,
        "backfill_ineligible": projection.backfill_ineligible,
    }


def load_verified_shadow_projection(
    profile: Mapping[str, Any],
    paths: ShadowRuntimePaths,
) -> ShadowProjection | None:
    """Verify storage and mathematically replay every shadow event from genesis."""

    from monitoring.ootang_calibration_shadow_ledger import (
        AppendOnlyShadowLedger,
        ShadowLedgerError,
    )

    if not paths.ledger.exists():
        return None
    if paths.ledger.is_symlink() or not paths.ledger.is_file():
        raise CalibrationShadowIntegrity(
            "shadow ledger path is not a regular non-symlink file"
        )
    code_sha = _implementation_sha256(profile)
    environment_sha = _environment_sha256()
    try:
        ledger = AppendOnlyShadowLedger(paths.ledger)
        events = ledger.read_events()
    except ShadowLedgerError as exc:
        raise CalibrationShadowIntegrity(
            f"append-only shadow ledger verification failed: {exc}"
        ) from exc
    if not events:
        raise CalibrationShadowIntegrity("shadow ledger has no epoch genesis")
    for event in events:
        _exact_event_context(event, profile, code_sha, environment_sha)

    core = _core(profile)
    projection: ShadowProjection | None = None
    offset = 0
    while offset < len(events):
        first = events[offset]
        transaction_size = first.transaction_size
        transaction = events[offset : offset + transaction_size]
        if len(transaction) != transaction_size:
            raise CalibrationShadowIntegrity("shadow transaction is incomplete")
        event_type = first.event_type

        if event_type == "shadow_epoch_genesis":
            if transaction_size != 1 or (
                projection is not None and projection.epoch_open
            ):
                raise CalibrationShadowIntegrity("shadow genesis lifecycle changed")
            payload = _event_payload(first, name="shadow.genesis")
            upstream_epoch = _require_text(
                payload.get("upstream_live_epoch_id"),
                name="shadow.genesis.upstream_epoch",
            )
            activation_sequence = payload.get("activation_live_cursor_sequence_id")
            if (
                not isinstance(activation_sequence, int)
                or isinstance(activation_sequence, bool)
                or activation_sequence < 1
            ):
                raise CalibrationShadowIntegrity(
                    "shadow genesis activation cursor sequence changed"
                )
            activation_cursor = (
                activation_sequence,
                _require_sha256(
                    payload.get("activation_live_cursor_sha256"),
                    name="shadow.genesis.activation_live_cursor",
                ),
            )
            previous_terminal = (
                ZERO_HASH if offset == 0 else events[offset - 1].entry_sha256
            )
            activation_target = payload.get("activation_outstanding_target_date")
            if activation_target is not None:
                activation_target = _require_date(
                    activation_target,
                    name="shadow.genesis.activation_outstanding_target_date",
                )
            epoch_id = _canonical_sha256(
                {
                    "schema_version": "shadow_epoch_identity_v1",
                    "profile_sha256": profile["_profile_sha256"],
                    "upstream_live_epoch_id": upstream_epoch,
                    "activation_live_cursor_sequence_id": activation_cursor[0],
                    "activation_live_cursor_sha256": activation_cursor[1],
                    "previous_shadow_epoch_terminal_sha256": previous_terminal,
                }
            )
            states = _fresh_states(profile, core)
            aggregate = _aggregate_state_sha(states, core)
            expected_payload = {
                "schema_version": "shadow_epoch_genesis_v1",
                "shadow_epoch_id": epoch_id,
                "upstream_live_epoch_id": upstream_epoch,
                "activation_live_cursor_sequence_id": activation_cursor[0],
                "activation_live_cursor_sha256": activation_cursor[1],
                "activation_outstanding_target_date": activation_target,
                "activation_outstanding_prospective_eligible": False,
                "cold_start_semantic_reason": profile["protocol"][
                    "cold_start_semantic_reason"
                ],
                "core_reset_token": profile["protocol"]["cold_start_core_reset_token"],
                "candidate_order": list(METHODS),
                "station_order": list(STATIONS),
                "candidate_settings": profile["candidates"],
                "candidate_settings_sha256": _canonical_sha256(profile["candidates"]),
                "evaluation_contract": profile["evaluation"],
                "evaluation_contract_sha256": _canonical_sha256(profile["evaluation"]),
                "initial_state_sha256": _state_hashes(states, core),
                "initial_state_aggregate_sha256": aggregate,
                "previous_shadow_epoch_terminal_sha256": previous_terminal,
                "full_replay_required": True,
                "support_metrics_replayable": True,
                "selection_performed": False,
                "promotion_performed": False,
                "automatic_promotion_enabled": False,
                "formal_warning_output": False,
                "e2_live_evidence_eligible": False,
            }
            _expect_payload(payload, expected_payload, name="shadow.genesis")
            _validate_event_envelope(
                first,
                event_key=f"{epoch_id}:genesis",
                event_type="shadow_epoch_genesis",
                target_date=None,
                station=None,
                issue_id=None,
                state_before_sha256=aggregate,
                state_after_sha256=aggregate,
            )
            history = _preserved_history(projection)
            projection = ShadowProjection(
                shadow_epoch_id=epoch_id,
                upstream_live_epoch_id=upstream_epoch,
                states=states,
                epoch_open=True,
                activation_outstanding_target_date=activation_target,
                sufficient_statistics=_new_statistics(),
                live_cursor_sequence_id=activation_cursor[0],
                live_cursor_sha256=activation_cursor[1],
                **history,
            )

        elif event_type == "shadow_issue_batch_opened":
            if projection is None or not projection.epoch_open:
                raise CalibrationShadowIntegrity("shadow issue precedes open genesis")
            expected_types = (
                "shadow_issue_batch_opened",
                *("shadow_candidate_issued" for _ in PAIR_ORDER),
                "shadow_issue_batch_sealed",
            )
            if tuple(event.event_type for event in transaction) != expected_types:
                raise CalibrationShadowIntegrity(
                    "shadow issue transaction boundary/order changed"
                )
            if projection.outstanding_target_date is not None:
                raise CalibrationShadowIntegrity(
                    "shadow issue overlaps outstanding issue"
                )
            opened = transaction[0]
            sealed = transaction[-1]
            opened_payload = _event_payload(opened, name="shadow.issue.opened")
            target = _require_date(
                opened_payload.get("target_date"), name="shadow.issue.target"
            )
            if (
                target in projection.issues_by_target
                or target in projection.backfill_ineligible
            ):
                raise CalibrationShadowIntegrity("shadow target was already classified")
            live_seal_sha = _require_sha256(
                opened_payload.get("live_issue_seal_entry_sha256"),
                name="shadow.issue.live_seal",
            )
            previous_issue_sha = _require_sha256(
                opened_payload.get("previous_issue_batch_sha256"),
                name="shadow.issue.previous",
            )
            if previous_issue_sha != projection.last_issue_batch_sha256:
                raise CalibrationShadowIntegrity("shadow issue-only chain forked")
            prospective = opened_payload.get(
                "engineering_prospective_ordering_candidate"
            )
            expected_prospective = (
                target != projection.activation_outstanding_target_date
            )
            if prospective is not expected_prospective:
                raise CalibrationShadowIntegrity(
                    "shadow issue prospective eligibility changed"
                )
            cursor = _payload_cursor(opened_payload, name="shadow.issue")
            aggregate = _aggregate_state_sha(projection.states, core)
            prefix = f"{projection.shadow_epoch_id}:{target}:issue"
            issue_id = _require_sha256(opened.issue_id, name="shadow.issue.id")
            expected_opened = {
                "schema_version": "shadow_issue_batch_opened_v1",
                "shadow_epoch_id": projection.shadow_epoch_id,
                "target_date": target,
                "live_issue_seal_entry_sha256": live_seal_sha,
                "previous_issue_batch_sha256": previous_issue_sha,
                "engineering_prospective_ordering_candidate": prospective,
                "candidate_count": len(PAIR_ORDER),
                "all_candidate_issues_prepared_before_append": True,
                "live_cursor_sequence_id": cursor[0],
                "live_cursor_sha256": cursor[1],
                "selection_performed": False,
                "promotion_performed": False,
                "formal_warning_output": False,
            }
            _assert_issue_only(opened_payload)
            _expect_payload(opened_payload, expected_opened, name="shadow.issue.opened")
            _validate_event_envelope(
                opened,
                event_key=f"{prefix}:opened",
                event_type="shadow_issue_batch_opened",
                target_date=target,
                station=None,
                issue_id=issue_id,
                state_before_sha256=aggregate,
                state_after_sha256=aggregate,
            )
            records: dict[tuple[str, str], _IssueRecord] = {}
            candidate_payloads: list[dict[str, Any]] = []
            for event, (method, station) in zip(
                transaction[1:-1], PAIR_ORDER, strict=True
            ):
                payload = _event_payload(event, name=f"shadow.issue.{method}.{station}")
                point = _finite(
                    payload.get("point_forecast_mm"),
                    name=f"shadow.issue.{method}.{station}.point",
                )
                state = projection.states[method][station]
                state_sha = core.state_sha256(state)
                issue = _issue(method, state, point, profile, core)
                live_station_sha = _require_sha256(
                    payload.get("live_station_issue_entry_sha256"),
                    name="shadow.issue.live_station_issue",
                )
                expected_candidate = {
                    "schema_version": "shadow_candidate_issue_v1",
                    "shadow_epoch_id": projection.shadow_epoch_id,
                    "target_date": target,
                    "method": method,
                    "station": station,
                    "point_forecast_mm": point,
                    "live_station_issue_entry_sha256": live_station_sha,
                    "state_before_sha256": state_sha,
                    "engineering_prospective_ordering_candidate": prospective,
                    "live_cursor_sequence_id": cursor[0],
                    "live_cursor_sha256": cursor[1],
                    "issue": _issue_payload(method, issue),
                    "selection_performed": False,
                    "promotion_performed": False,
                    "formal_warning_output": False,
                }
                _assert_issue_only(payload)
                _expect_payload(
                    payload,
                    expected_candidate,
                    name=f"shadow.issue.{method}.{station}",
                )
                _validate_event_envelope(
                    event,
                    event_key=f"{prefix}:candidate:{method}:{station}",
                    event_type="shadow_candidate_issued",
                    target_date=target,
                    station=station,
                    issue_id=issue_id,
                    state_before_sha256=state_sha,
                    state_after_sha256=state_sha,
                )
                if (
                    event.input_manifest_sha256 != opened.input_manifest_sha256
                    or event.model_manifest_sha256 != opened.model_manifest_sha256
                ):
                    raise CalibrationShadowIntegrity(
                        "shadow issue transaction live artifact binding changed"
                    )
                candidate_payloads.append(expected_candidate)
                records[(method, station)] = _IssueRecord(
                    epoch_id=projection.shadow_epoch_id,
                    target_date=target,
                    method=method,
                    station=station,
                    point_forecast_mm=point,
                    live_station_issue_entry_sha256=live_station_sha,
                    issue=issue,
                    issue_payload=expected_candidate,
                    state_before_sha256=state_sha,
                    prospective_eligible=bool(prospective),
                )
            expected_issue_sha = _issue_batch_scientific_sha(
                epoch_id=projection.shadow_epoch_id,
                target=target,
                previous_issue_batch_sha256=previous_issue_sha,
                live_seal_sha256=live_seal_sha,
                prospective_eligible=bool(prospective),
                candidate_payloads=candidate_payloads,
            )
            if issue_id != expected_issue_sha:
                raise CalibrationShadowIntegrity("shadow issue batch digest changed")
            sealed_payload = _event_payload(sealed, name="shadow.issue.sealed")
            expected_sealed = {
                "schema_version": "shadow_issue_batch_sealed_v1",
                "shadow_epoch_id": projection.shadow_epoch_id,
                "target_date": target,
                "live_issue_seal_entry_sha256": live_seal_sha,
                "previous_issue_batch_sha256": previous_issue_sha,
                "issue_batch_sha256": expected_issue_sha,
                "engineering_prospective_ordering_candidate": prospective,
                "candidate_event_keys": [
                    f"{prefix}:candidate:{method}:{station}"
                    for method, station in PAIR_ORDER
                ],
                "candidate_count": len(PAIR_ORDER),
                "same_date_information_excluded": True,
                "live_cursor_sequence_id": cursor[0],
                "live_cursor_sha256": cursor[1],
                "selection_performed": False,
                "promotion_performed": False,
                "formal_warning_output": False,
            }
            _assert_issue_only(sealed_payload)
            _expect_payload(sealed_payload, expected_sealed, name="shadow.issue.sealed")
            _validate_event_envelope(
                sealed,
                event_key=f"{prefix}:sealed",
                event_type="shadow_issue_batch_sealed",
                target_date=target,
                station=None,
                issue_id=expected_issue_sha,
                state_before_sha256=aggregate,
                state_after_sha256=aggregate,
            )
            if (
                sealed.input_manifest_sha256 != opened.input_manifest_sha256
                or sealed.model_manifest_sha256 != opened.model_manifest_sha256
            ):
                raise CalibrationShadowIntegrity(
                    "shadow issue seal artifact binding changed"
                )
            projection.last_issue_batch_sha256 = expected_issue_sha
            projection.outstanding_target_date = target
            projection.outstanding_issue_batch_sha256 = expected_issue_sha
            projection.outstanding_live_seal_entry_sha256 = live_seal_sha
            projection.outstanding_prospective_eligible = bool(prospective)
            projection.outstanding_issues = records
            projection.issues_by_target[target] = dict(records)
            projection.target_epoch[target] = projection.shadow_epoch_id
            _advance_projection_cursor(projection, cursor)

        elif event_type == "shadow_outcome_batch_opened":
            if projection is None or not projection.epoch_open:
                raise CalibrationShadowIntegrity("shadow outcome precedes open genesis")
            expected_types = (
                "shadow_outcome_batch_opened",
                *("shadow_candidate_revealed" for _ in PAIR_ORDER),
                *("shadow_state_updated" for _ in PAIR_ORDER),
                "shadow_outcome_batch_settled",
            )
            if tuple(event.event_type for event in transaction) != expected_types:
                raise CalibrationShadowIntegrity(
                    "shadow outcome transaction boundary/order changed"
                )
            if projection.outstanding_target_date is None:
                raise CalibrationShadowIntegrity("shadow outcome has no sealed issue")
            opened = transaction[0]
            settled_event = transaction[-1]
            opened_payload = _event_payload(opened, name="shadow.outcome.opened")
            target = _require_date(
                opened_payload.get("target_date"), name="shadow.outcome.target"
            )
            if target != projection.outstanding_target_date:
                raise CalibrationShadowIntegrity("shadow outcome target changed")
            revision_id = _require_text(
                opened_payload.get("source_revision_id"),
                name="shadow.outcome.revision",
            )
            live_batch_sha = _require_sha256(
                opened_payload.get("live_outcome_batch_sha256"),
                name="shadow.outcome.live_batch",
            )
            live_settlement_sha = _require_sha256(
                opened_payload.get("live_settlement_entry_sha256"),
                name="shadow.outcome.live_settlement",
            )
            live_seal_sha = _require_sha256(
                opened_payload.get("live_issue_seal_entry_sha256"),
                name="shadow.outcome.live_seal",
            )
            issue_batch_sha = projection.outstanding_issue_batch_sha256
            if (
                live_seal_sha != projection.outstanding_live_seal_entry_sha256
                or opened_payload.get("shadow_issue_batch_sha256") != issue_batch_sha
            ):
                raise CalibrationShadowIntegrity("shadow outcome seal binding changed")
            cursor = _payload_cursor(opened_payload, name="shadow.outcome")
            prospective = projection.outstanding_prospective_eligible
            initial_aggregate = _aggregate_state_sha(projection.states, core)
            revision_token = _sha256_bytes(revision_id.encode("utf-8"))
            prefix = f"{projection.shadow_epoch_id}:{target}:outcome:{revision_token}"
            expected_opened = {
                "schema_version": "shadow_outcome_batch_opened_v1",
                "shadow_epoch_id": projection.shadow_epoch_id,
                "target_date": target,
                "source_revision_id": revision_id,
                "live_outcome_batch_sha256": live_batch_sha,
                "live_settlement_entry_sha256": live_settlement_sha,
                "live_issue_seal_entry_sha256": live_seal_sha,
                "shadow_issue_batch_sha256": issue_batch_sha,
                "all_eight_actuals_loaded_after_shadow_seal": True,
                "engineering_prospective_ordering_candidate": prospective,
                "live_cursor_sequence_id": cursor[0],
                "live_cursor_sha256": cursor[1],
                "promotion_performed": False,
                "formal_warning_output": False,
            }
            _expect_payload(
                opened_payload, expected_opened, name="shadow.outcome.opened"
            )
            _validate_event_envelope(
                opened,
                event_key=f"{prefix}:opened",
                event_type="shadow_outcome_batch_opened",
                target_date=target,
                station=None,
                issue_id=issue_batch_sha,
                state_before_sha256=initial_aggregate,
                state_after_sha256=initial_aggregate,
            )
            reveal_payloads: list[dict[str, Any]] = []
            reveal_objects: dict[tuple[str, str], object] = {}
            actuals: dict[str, float] = {}
            for event, (method, station) in zip(
                transaction[1 : 1 + len(PAIR_ORDER)], PAIR_ORDER, strict=True
            ):
                payload = _event_payload(
                    event, name=f"shadow.reveal.{method}.{station}"
                )
                actual = _finite(
                    payload.get("actual_mm"),
                    name=f"shadow.reveal.{method}.{station}.actual",
                )
                known_actual = actuals.setdefault(station, actual)
                if known_actual != actual:
                    raise CalibrationShadowIntegrity(
                        "shadow methods saw different station actuals"
                    )
                record = projection.outstanding_issues[(method, station)]
                state = projection.states[method][station]
                state_sha = core.state_sha256(state)
                reveal = _reveal(method, state, record.issue, actual, core)
                reveal_objects[(method, station)] = reveal
                expected_reveal = {
                    "schema_version": "shadow_candidate_reveal_v1",
                    "shadow_epoch_id": projection.shadow_epoch_id,
                    "target_date": target,
                    "method": method,
                    "station": station,
                    "actual_mm": actual,
                    "source_revision_id": revision_id,
                    "live_settlement_entry_sha256": live_settlement_sha,
                    "live_issue_seal_entry_sha256": live_seal_sha,
                    "shadow_issue_batch_sha256": issue_batch_sha,
                    "shadow_issue_state_sha256": record.state_before_sha256,
                    "engineering_prospective_ordering_candidate": prospective,
                    "reveal": _reveal_payload(method, reveal),
                    "metrics": _interval_metrics(record.issue, actual),
                    "live_cursor_sequence_id": cursor[0],
                    "live_cursor_sha256": cursor[1],
                    "promotion_performed": False,
                    "formal_warning_output": False,
                }
                _expect_payload(
                    payload,
                    expected_reveal,
                    name=f"shadow.reveal.{method}.{station}",
                )
                _validate_event_envelope(
                    event,
                    event_key=f"{prefix}:candidate:{method}:{station}",
                    event_type="shadow_candidate_revealed",
                    target_date=target,
                    station=station,
                    issue_id=issue_batch_sha,
                    state_before_sha256=state_sha,
                    state_after_sha256=state_sha,
                )
                if (
                    event.input_manifest_sha256 != opened.input_manifest_sha256
                    or event.model_manifest_sha256 != opened.model_manifest_sha256
                ):
                    raise CalibrationShadowIntegrity(
                        "shadow reveal live artifact binding changed"
                    )
                reveal_payloads.append(expected_reveal)
            if tuple(actuals) != STATIONS:
                raise CalibrationShadowIntegrity("shadow actual station order changed")
            next_states = {
                method: dict(projection.states[method]) for method in METHODS
            }
            update_payloads: list[dict[str, Any]] = []
            state_start = 1 + len(PAIR_ORDER)
            for event, (method, station) in zip(
                transaction[state_start:-1], PAIR_ORDER, strict=True
            ):
                payload = _event_payload(event, name=f"shadow.state.{method}.{station}")
                drift_hash = _require_sha256(
                    payload.get("live_drift_event_entry_sha256"),
                    name="shadow.state.live_drift",
                )
                drift_detected = payload.get("live_drift_detected")
                if not isinstance(drift_detected, bool):
                    raise CalibrationShadowIntegrity(
                        "shadow state drift flag is not boolean"
                    )
                reveal = reveal_objects[(method, station)]
                updated_state = reveal.updated_state
                effective_state = (
                    _fresh_state_after_drift(method, profile, core)
                    if drift_detected
                    else updated_state
                )
                before = core.state_sha256(projection.states[method][station])
                after = core.state_sha256(effective_state)
                expected_update = {
                    "schema_version": "shadow_state_update_v1",
                    "shadow_epoch_id": projection.shadow_epoch_id,
                    "target_date": target,
                    "method": method,
                    "station": station,
                    "source_revision_id": revision_id,
                    "live_drift_event_entry_sha256": drift_hash,
                    "live_drift_detected": drift_detected,
                    "updated_state_sha256": core.state_sha256(updated_state),
                    "effective_next_state_sha256": after,
                    "effective_next_state": _state_payload(effective_state, core),
                    "reset_reason": effective_state.reset_reason,
                    "reset_applies_next_target_date": True,
                    "live_online_state_affected": False,
                    "live_cursor_sequence_id": cursor[0],
                    "live_cursor_sha256": cursor[1],
                    "promotion_performed": False,
                    "formal_warning_output": False,
                }
                _expect_payload(
                    payload,
                    expected_update,
                    name=f"shadow.state.{method}.{station}",
                )
                _validate_event_envelope(
                    event,
                    event_key=f"{prefix}:state:{method}:{station}",
                    event_type="shadow_state_updated",
                    target_date=target,
                    station=station,
                    issue_id=issue_batch_sha,
                    state_before_sha256=before,
                    state_after_sha256=after,
                )
                if (
                    event.input_manifest_sha256 != opened.input_manifest_sha256
                    or event.model_manifest_sha256 != opened.model_manifest_sha256
                ):
                    raise CalibrationShadowIntegrity(
                        "shadow state live artifact binding changed"
                    )
                next_states[method][station] = effective_state
                update_payloads.append(expected_update)
            metrics, station_joint, joint_all = _metrics_for_all(
                projection.outstanding_issues, actuals
            )
            final_aggregate = _aggregate_state_sha(next_states, core)
            shadow_outcome_sha = _canonical_sha256(
                {
                    "schema_version": "shadow_outcome_batch_digest_v1",
                    "shadow_epoch_id": projection.shadow_epoch_id,
                    "target_date": target,
                    "live_settlement_entry_sha256": live_settlement_sha,
                    "live_outcome_batch_sha256": live_batch_sha,
                    "shadow_issue_batch_sha256": issue_batch_sha,
                    "reveals": reveal_payloads,
                    "state_updates": update_payloads,
                }
            )
            settled_payload = _event_payload(
                settled_event, name="shadow.outcome.settled"
            )
            expected_settled = {
                "schema_version": "shadow_outcome_batch_settled_v1",
                "shadow_epoch_id": projection.shadow_epoch_id,
                "target_date": target,
                "source_revision_id": revision_id,
                "live_outcome_batch_sha256": live_batch_sha,
                "live_settlement_entry_sha256": live_settlement_sha,
                "live_issue_seal_entry_sha256": live_seal_sha,
                "shadow_issue_batch_sha256": issue_batch_sha,
                "shadow_outcome_batch_sha256": shadow_outcome_sha,
                "actual_by_station": actuals,
                "engineering_prospective_ordering_candidate": prospective,
                "metrics": metrics,
                "joint_available_by_station": station_joint,
                "all_station_joint_available": joint_all,
                "revision_backfill_ineligible_excluded": True,
                "support_metrics_replayable": True,
                "live_cursor_sequence_id": cursor[0],
                "live_cursor_sha256": cursor[1],
                "selection_performed": False,
                "promotion_performed": False,
                "automatic_promotion_enabled": False,
                "formal_warning_output": False,
            }
            _expect_payload(
                settled_payload,
                expected_settled,
                name="shadow.outcome.settled",
            )
            _validate_event_envelope(
                settled_event,
                event_key=f"{prefix}:settled",
                event_type="shadow_outcome_batch_settled",
                target_date=target,
                station=None,
                issue_id=issue_batch_sha,
                state_before_sha256=initial_aggregate,
                state_after_sha256=final_aggregate,
            )
            if (
                settled_event.input_manifest_sha256 != opened.input_manifest_sha256
                or settled_event.model_manifest_sha256 != opened.model_manifest_sha256
            ):
                raise CalibrationShadowIntegrity(
                    "shadow settlement live artifact binding changed"
                )
            projection.states = next_states
            _update_statistics(
                projection,
                metrics,
                station_joint,
                prospective_eligible=prospective,
            )
            projection.eligible_daily.append(
                {
                    "target_date": target,
                    "shadow_epoch_id": projection.shadow_epoch_id,
                    "engineering_prospective_ordering_candidate": prospective,
                    "metrics": metrics,
                    "joint_available_by_station": station_joint,
                    "all_station_joint_available": joint_all,
                }
            )
            projection.settled[target] = {
                "shadow_epoch_id": projection.shadow_epoch_id,
                "source_revision_id": revision_id,
                "live_outcome_batch_sha256": live_batch_sha,
                "live_settlement_entry_sha256": live_settlement_sha,
                "shadow_issue_batch_sha256": issue_batch_sha,
                "shadow_outcome_batch_sha256": shadow_outcome_sha,
                "shadow_settlement_entry_sha256": settled_event.entry_sha256,
                "engineering_prospective_ordering_candidate": prospective,
            }
            projection.revisions[target] = {revision_id: live_batch_sha}
            projection.outstanding_target_date = None
            projection.outstanding_issue_batch_sha256 = None
            projection.outstanding_live_seal_entry_sha256 = None
            projection.outstanding_prospective_eligible = False
            projection.outstanding_issues = {}
            _advance_projection_cursor(projection, cursor)

        elif event_type == "shadow_backfill_ineligible":
            if projection is None or not projection.epoch_open or transaction_size != 1:
                raise CalibrationShadowIntegrity("shadow backfill lifecycle changed")
            payload = _event_payload(first, name="shadow.backfill")
            target = _require_date(
                payload.get("target_date"), name="shadow.backfill.target"
            )
            if (
                target in projection.backfill_ineligible
                or target in projection.issues_by_target
                or target in projection.settled
            ):
                raise CalibrationShadowIntegrity(
                    "shadow target classification repeated"
                )
            source_kind = payload.get("source_kind")
            expected_reason = {
                "settlement_without_shadow_issue": (
                    "live_settlement_preceded_durable_shadow_issue_seal"
                ),
                "upstream_backfill_not_blind": (
                    "live_classified_target_as_backfill_before_shadow_issue"
                ),
            }.get(source_kind)
            if expected_reason is None:
                raise CalibrationShadowIntegrity("shadow backfill source kind changed")
            live_entry = _require_sha256(
                payload.get("live_source_entry_sha256"),
                name="shadow.backfill.live_entry",
            )
            live_batch = _require_sha256(
                payload.get("live_outcome_batch_sha256"),
                name="shadow.backfill.live_batch",
            )
            revision_id = _require_text(
                payload.get("source_revision_id"),
                name="shadow.backfill.revision",
            )
            live_seal = payload.get("live_issue_seal_entry_sha256")
            if source_kind == "settlement_without_shadow_issue":
                live_seal = _require_sha256(live_seal, name="shadow.backfill.live_seal")
            elif live_seal is not None:
                raise CalibrationShadowIntegrity(
                    "upstream backfill unexpectedly references an issue seal"
                )
            cursor = _payload_cursor(payload, name="shadow.backfill")
            expected_payload = {
                "schema_version": "shadow_backfill_ineligible_v1",
                "shadow_epoch_id": projection.shadow_epoch_id,
                "target_date": target,
                "reason": expected_reason,
                "source_kind": source_kind,
                "live_source_entry_sha256": live_entry,
                "live_outcome_batch_sha256": live_batch,
                "source_revision_id": revision_id,
                "live_issue_seal_entry_sha256": live_seal,
                "shadow_issue_created": False,
                "shadow_state_updated": False,
                "eligible_metrics_updated": False,
                "retrospective_promotion_prohibited": True,
                "live_cursor_sequence_id": cursor[0],
                "live_cursor_sha256": cursor[1],
                "selection_performed": False,
                "promotion_performed": False,
                "formal_warning_output": False,
            }
            _expect_payload(payload, expected_payload, name="shadow.backfill")
            aggregate = _aggregate_state_sha(projection.states, core)
            _validate_event_envelope(
                first,
                event_key=(
                    f"{projection.shadow_epoch_id}:{target}:backfill_ineligible"
                ),
                event_type="shadow_backfill_ineligible",
                target_date=target,
                station=None,
                issue_id=None,
                state_before_sha256=aggregate,
                state_after_sha256=aggregate,
            )
            projection.backfill_ineligible[target] = live_entry
            projection.target_epoch[target] = projection.shadow_epoch_id
            _advance_projection_cursor(projection, cursor)

        elif event_type == "shadow_outcome_revision_rescored":
            if projection is None or not projection.epoch_open:
                raise CalibrationShadowIntegrity(
                    "shadow revision precedes open genesis"
                )
            if transaction_size != len(PAIR_ORDER) or any(
                event.event_type != "shadow_outcome_revision_rescored"
                for event in transaction
            ):
                raise CalibrationShadowIntegrity(
                    "shadow revision transaction boundary changed"
                )
            first_payload = _event_payload(first, name="shadow.revision.first")
            target = _require_date(
                first_payload.get("target_date"), name="shadow.revision.target"
            )
            revision_id = _require_text(
                first_payload.get("source_revision_id"),
                name="shadow.revision.id",
            )
            live_batch_sha = _require_sha256(
                first_payload.get("live_outcome_batch_sha256"),
                name="shadow.revision.batch",
            )
            if (
                target not in projection.settled
                or target not in projection.issues_by_target
                or revision_id in projection.revisions.get(target, {})
            ):
                raise CalibrationShadowIntegrity("shadow revision lineage changed")
            target_epoch = projection.target_epoch[target]
            issue_batch_sha = projection.settled[target]["shadow_issue_batch_sha256"]
            original_outcome_sha = projection.settled[target][
                "shadow_outcome_batch_sha256"
            ]
            revision_token = _sha256_bytes(revision_id.encode("utf-8"))
            prefix = f"{target_epoch}:{target}:revision:{revision_token}"
            cursor: tuple[int, str] | None = None
            station_actuals: dict[str, float] = {}
            for event, (method, station) in zip(transaction, PAIR_ORDER, strict=True):
                payload = _event_payload(
                    event, name=f"shadow.revision.{method}.{station}"
                )
                revised_actual = _finite(
                    payload.get("revised_actual_mm"),
                    name=f"shadow.revision.{method}.{station}.actual",
                )
                known = station_actuals.setdefault(station, revised_actual)
                if known != revised_actual:
                    raise CalibrationShadowIntegrity(
                        "shadow revision methods saw different actuals"
                    )
                live_revision_sha = _require_sha256(
                    payload.get("live_revision_entry_sha256"),
                    name="shadow.revision.live_entry",
                )
                event_cursor = _payload_cursor(payload, name="shadow.revision")
                if cursor is None:
                    cursor = event_cursor
                elif cursor != event_cursor:
                    raise CalibrationShadowIntegrity(
                        "shadow revision cursor changed inside transaction"
                    )
                state_sha = core.state_sha256(projection.states[method][station])
                issue = projection.issues_by_target[target][(method, station)]
                expected_payload = {
                    "schema_version": "shadow_outcome_revision_rescore_v1",
                    "shadow_epoch_id": target_epoch,
                    "target_date": target,
                    "method": method,
                    "station": station,
                    "source_revision_id": revision_id,
                    "live_outcome_batch_sha256": live_batch_sha,
                    "live_revision_entry_sha256": live_revision_sha,
                    "original_shadow_issue_batch_sha256": issue_batch_sha,
                    "original_shadow_outcome_batch_sha256": (original_outcome_sha),
                    "revised_actual_mm": revised_actual,
                    "metrics": _interval_metrics(issue.issue, revised_actual),
                    "state_before_sha256": state_sha,
                    "state_after_sha256": state_sha,
                    "online_state_updated": False,
                    "eligible_metrics_updated": False,
                    "retrospective_view_only": True,
                    "retrospective_promotion_prohibited": True,
                    "live_cursor_sequence_id": event_cursor[0],
                    "live_cursor_sha256": event_cursor[1],
                    "selection_performed": False,
                    "promotion_performed": False,
                    "formal_warning_output": False,
                }
                _expect_payload(
                    payload,
                    expected_payload,
                    name=f"shadow.revision.{method}.{station}",
                )
                _validate_event_envelope(
                    event,
                    event_key=f"{prefix}:{method}:{station}",
                    event_type="shadow_outcome_revision_rescored",
                    target_date=target,
                    station=station,
                    issue_id=issue_batch_sha,
                    state_before_sha256=state_sha,
                    state_after_sha256=state_sha,
                )
            if cursor is None:  # pragma: no cover - fixed nonempty PAIR_ORDER
                raise CalibrationShadowIntegrity("shadow revision is empty")
            projection.revisions.setdefault(target, {})[revision_id] = live_batch_sha
            _advance_projection_cursor(projection, cursor)

        elif event_type == "shadow_epoch_closed":
            if (
                projection is None
                or not projection.epoch_open
                or transaction_size != 1
                or projection.outstanding_target_date is not None
            ):
                raise CalibrationShadowIntegrity("shadow epoch close lifecycle changed")
            payload = _event_payload(first, name="shadow.epoch_closed")
            next_epoch = _require_text(
                payload.get("next_upstream_live_epoch_id"),
                name="shadow.close.next_epoch",
            )
            if next_epoch == projection.upstream_live_epoch_id:
                raise CalibrationShadowIntegrity("shadow epoch close did not rotate")
            cursor = _payload_cursor(payload, name="shadow.close")
            aggregate = _aggregate_state_sha(projection.states, core)
            expected_payload = {
                "schema_version": "shadow_epoch_closed_v1",
                "shadow_epoch_id": projection.shadow_epoch_id,
                "upstream_live_epoch_id": projection.upstream_live_epoch_id,
                "next_upstream_live_epoch_id": next_epoch,
                "reason": "verified_upstream_live_epoch_changed",
                "terminal_state_sha256": _state_hashes(projection.states, core),
                "terminal_state_aggregate_sha256": aggregate,
                "live_cursor_sequence_id": cursor[0],
                "live_cursor_sha256": cursor[1],
                "selection_performed": False,
                "promotion_performed": False,
                "formal_warning_output": False,
            }
            _expect_payload(payload, expected_payload, name="shadow.epoch_closed")
            _validate_event_envelope(
                first,
                event_key=(f"{projection.shadow_epoch_id}:close:{next_epoch}"),
                event_type="shadow_epoch_closed",
                target_date=None,
                station=None,
                issue_id=None,
                state_before_sha256=aggregate,
                state_after_sha256=aggregate,
            )
            projection.epoch_open = False
            projection.live_cursor_sequence_id = cursor[0]
            projection.live_cursor_sha256 = cursor[1]

        elif event_type == "shadow_integrity_blocked":
            raise CalibrationShadowIntegrity(
                "shadow ledger contains a durable integrity-block marker"
            )
        else:
            raise CalibrationShadowIntegrity(
                f"shadow transaction starts with unsupported event {event_type!r}"
            )
        offset += transaction_size

    if projection is None:  # pragma: no cover - genesis branch establishes it
        raise CalibrationShadowIntegrity("shadow ledger projection is absent")
    head = events[-1]
    projection.ledger_events = tuple(events)
    projection.ledger_event_count = len(events)
    projection.ledger_terminal_sequence_id = head.sequence_id
    projection.ledger_terminal_sha256 = head.entry_sha256
    return projection


def _validate_shadow_live_binding(
    projection: ShadowProjection, live_projection: object
) -> None:
    if projection.upstream_live_epoch_id != str(live_projection.epoch_id):
        return
    live_events = tuple(live_projection.ledger_events)
    live_map = _live_event_map(live_projection)
    if projection.live_cursor_sequence_id > len(live_events):
        raise CalibrationShadowIntegrity(
            "shadow cursor is ahead of verified live ledger"
        )
    cursor_event = live_events[projection.live_cursor_sequence_id - 1]
    if cursor_event.entry_sha256 != projection.live_cursor_sha256:
        raise CalibrationShadowIntegrity("shadow cursor is not on verified live chain")
    for target, records in projection.issues_by_target.items():
        for (method, station), record in records.items():
            del method
            live_event = live_map.get(record.live_station_issue_entry_sha256)
            if (
                live_event is None
                or getattr(live_event, "event_type", None) != "station_issue"
                or getattr(live_event, "target_date", None) != target
                or getattr(live_event, "station", None) != station
            ):
                raise CalibrationShadowIntegrity(
                    "shadow issue no longer binds a verified live station issue"
                )
            live_issue = _event_payload(live_event, name="live.bound_issue").get(
                "issue"
            )
            if (
                not isinstance(live_issue, Mapping)
                or _finite(
                    live_issue.get("point_forecast_mm"),
                    name="live.bound_issue.point",
                )
                != record.point_forecast_mm
            ):
                raise CalibrationShadowIntegrity(
                    "shadow issue point no longer matches verified live issue"
                )
    for target, record in projection.settled.items():
        live_event = live_map.get(record["live_settlement_entry_sha256"])
        if (
            live_event is None
            or getattr(live_event, "event_type", None) != "outcome_batch_settled"
            or getattr(live_event, "target_date", None) != target
        ):
            raise CalibrationShadowIntegrity(
                "shadow settlement no longer binds verified live settlement"
            )
    for target, live_hash in projection.backfill_ineligible.items():
        live_event = live_map.get(live_hash)
        if (
            live_event is None
            or getattr(live_event, "event_type", None)
            not in {"outcome_batch_settled", "backfill_not_blind"}
            or getattr(live_event, "target_date", None) != target
        ):
            raise CalibrationShadowIntegrity(
                "shadow backfill no longer binds verified live source"
            )
    for event in projection.ledger_events:
        if event.event_type != "shadow_outcome_revision_rescored":
            continue
        live_hash = _event_payload(event, name="shadow.bound_revision").get(
            "live_revision_entry_sha256"
        )
        live_event = live_map.get(live_hash)
        if (
            live_event is None
            or getattr(live_event, "event_type", None) != "outcome_revision"
            or getattr(live_event, "target_date", None) != event.target_date
            or getattr(live_event, "station", None) != event.station
        ):
            raise CalibrationShadowIntegrity(
                "shadow revision no longer binds verified live revision"
            )


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0.0:
        return 1.0 if numerator == 0.0 else None
    return numerator / denominator


def _engineering_readiness(
    projection: ShadowProjection, profile: Mapping[str, Any]
) -> dict[str, Any]:
    """Replay the predeclared engineering gates without selecting a method."""

    contract = profile["evaluation"]
    minimum_joint = int(contract["minimum_joint_available_target_dates"])
    minimum_station = int(contract["minimum_station_dates_per_method"])
    support_by_pair = {
        method: {
            station: int(
                projection.sufficient_statistics[method][station][
                    "joint_available_count"
                ]
            )
            for station in STATIONS
        }
        for method in METHODS
    }
    support_sufficient = (
        projection.joint_available_target_dates >= minimum_joint
        and all(
            support_by_pair[method][station] >= minimum_station
            for method, station in PAIR_ORDER
        )
    )
    rolling_count = int(contract["rolling_window_dates"])
    eligible_days = [
        row
        for row in projection.eligible_daily
        if row["engineering_prospective_ordering_candidate"] is True
    ]
    rolling_days = eligible_days[-rolling_count:]

    summaries: dict[str, dict[str, dict[str, Any]]] = {method: {} for method in METHODS}
    rolling_summaries: dict[str, dict[str, dict[str, Any]]] = {
        method: {} for method in METHODS
    }
    for method, station in PAIR_ORDER:
        statistics = projection.sufficient_statistics[method][station]
        eligible_count = int(statistics["eligible_settled_count"])
        available_count = int(statistics["interval_available_count"])
        joint_count = int(statistics["joint_available_count"])
        covered_count = int(statistics["joint_covered_count"])
        score_sum = float(statistics["joint_interval_score_sum"])
        summaries[method][station] = {
            "eligible_settled_count": eligible_count,
            "interval_available_count": available_count,
            "joint_available_count": joint_count,
            "joint_covered_count": covered_count,
            "availability": (
                None if eligible_count == 0 else available_count / eligible_count
            ),
            "joint_coverage": (
                None if joint_count == 0 else covered_count / joint_count
            ),
            "joint_coverage_absolute_gap": (
                None
                if joint_count == 0
                else abs(covered_count / joint_count - contract["target_coverage"])
            ),
            "joint_interval_score_mean": (
                None if joint_count == 0 else score_sum / joint_count
            ),
        }
        rolling_available = 0
        rolling_joint = 0
        rolling_covered = 0
        rolling_score_sum = 0.0
        for day in rolling_days:
            metric = day["metrics"][method][station]
            if metric["interval_available"]:
                rolling_available += 1
            if day["joint_available_by_station"][station]:
                rolling_joint += 1
                if metric["interval_covered"]:
                    rolling_covered += 1
                rolling_score_sum += float(metric["interval_score_80_mm"])
        rolling_summaries[method][station] = {
            "window_eligible_date_count": len(rolling_days),
            "interval_available_count": rolling_available,
            "joint_available_count": rolling_joint,
            "joint_covered_count": rolling_covered,
            "availability": (
                None if not rolling_days else rolling_available / len(rolling_days)
            ),
            "joint_coverage": (
                None if rolling_joint == 0 else rolling_covered / rolling_joint
            ),
            "joint_coverage_absolute_gap": (
                None
                if rolling_joint == 0
                else abs(rolling_covered / rolling_joint - contract["target_coverage"])
            ),
            "joint_interval_score_mean": (
                None if rolling_joint == 0 else rolling_score_sum / rolling_joint
            ),
        }

    pair_gates: dict[str, dict[str, dict[str, Any]]] = {
        method: {} for method in METHODS
    }
    for method, station in PAIR_ORDER:
        summary = summaries[method][station]
        rolling = rolling_summaries[method][station]
        control = summaries["aci_v1_control"][station]
        rolling_control = rolling_summaries["aci_v1_control"][station]
        coverage_gap = summary["joint_coverage_absolute_gap"]
        rolling_gap = rolling["joint_coverage_absolute_gap"]
        availability = summary["availability"]
        rolling_availability = rolling["availability"]
        score = summary["joint_interval_score_mean"]
        control_score = control["joint_interval_score_mean"]
        rolling_score = rolling["joint_interval_score_mean"]
        rolling_control_score = rolling_control["joint_interval_score_mean"]
        score_ratio = (
            None
            if score is None or control_score is None
            else _safe_ratio(score, control_score)
        )
        rolling_score_ratio = (
            None
            if rolling_score is None or rolling_control_score is None
            else _safe_ratio(rolling_score, rolling_control_score)
        )
        if method == "aci_v1_control":
            comparative_coverage_pass = True
            comparative_score_pass = True
            rolling_comparative_coverage_pass = True
            rolling_comparative_score_pass = True
        else:
            control_gap = control["joint_coverage_absolute_gap"]
            rolling_control_gap = rolling_control["joint_coverage_absolute_gap"]
            comparative_coverage_pass = (
                coverage_gap is not None
                and control_gap is not None
                and coverage_gap
                <= control_gap + contract["challenger_vs_aci_coverage_gap_margin"]
            )
            comparative_score_pass = (
                score_ratio is not None
                and score_ratio <= contract["challenger_interval_score_ratio_max"]
            )
            rolling_comparative_coverage_pass = (
                rolling_gap is not None
                and rolling_control_gap is not None
                and rolling_gap
                <= rolling_control_gap
                + contract["challenger_vs_aci_coverage_gap_margin"]
            )
            rolling_comparative_score_pass = (
                rolling_score_ratio is not None
                and rolling_score_ratio
                <= contract["challenger_interval_score_ratio_max"]
            )
        absolute_pass = (
            coverage_gap is not None
            and coverage_gap <= contract["coverage_absolute_gap_max"]
        )
        availability_pass = (
            availability is not None and availability >= contract["availability_min"]
        )
        rolling_complete = len(rolling_days) == rolling_count
        rolling_absolute_pass = (
            rolling_complete
            and rolling_gap is not None
            and rolling_gap <= contract["coverage_absolute_gap_max"]
        )
        rolling_availability_pass = (
            rolling_complete
            and rolling_availability is not None
            and rolling_availability >= contract["availability_min"]
        )
        passed = (
            support_sufficient
            and absolute_pass
            and availability_pass
            and comparative_coverage_pass
            and comparative_score_pass
            and rolling_absolute_pass
            and rolling_availability_pass
            and rolling_comparative_coverage_pass
            and rolling_comparative_score_pass
        )
        pair_gates[method][station] = {
            "support_sufficient": (support_by_pair[method][station] >= minimum_station),
            "coverage_absolute_gap_pass": absolute_pass,
            "availability_pass": availability_pass,
            "challenger_vs_aci_coverage_gap_pass": comparative_coverage_pass,
            "challenger_vs_aci_interval_score_ratio": score_ratio,
            "challenger_vs_aci_interval_score_pass": comparative_score_pass,
            "rolling_window_complete": rolling_complete,
            "rolling_coverage_absolute_gap_pass": rolling_absolute_pass,
            "rolling_availability_pass": rolling_availability_pass,
            "rolling_challenger_vs_aci_coverage_gap_pass": (
                rolling_comparative_coverage_pass
            ),
            "rolling_challenger_vs_aci_interval_score_ratio": (rolling_score_ratio),
            "rolling_challenger_vs_aci_interval_score_pass": (
                rolling_comparative_score_pass
            ),
            "engineering_gate_passed": passed,
        }
    method_all_station = {
        method: all(
            pair_gates[method][station]["engineering_gate_passed"]
            for station in STATIONS
        )
        for method in METHODS
    }
    all_required = all(method_all_station.values())
    if not support_sufficient:
        overall = "insufficient_prospective_support"
    elif all_required:
        overall = "engineering_readiness_gates_passed"
    else:
        overall = "engineering_readiness_gates_not_passed"
    return _json_value(
        {
            "schema_version": "shadow_engineering_readiness_v1",
            "evaluation_implemented": True,
            "overall": overall,
            "support_sufficient": support_sufficient,
            "joint_available_target_dates": (projection.joint_available_target_dates),
            "minimum_joint_available_target_dates": minimum_joint,
            "support_by_method_station": support_by_pair,
            "minimum_station_dates_per_method": minimum_station,
            "rolling_window_dates": rolling_count,
            "rolling_window_eligible_date_count": len(rolling_days),
            "method_station_metrics": summaries,
            "rolling_method_station_metrics": rolling_summaries,
            "method_station_gates": pair_gates,
            "method_all_station_gate_passed": method_all_station,
            "all_station_gate_required": True,
            "all_required_engineering_gates_passed": all_required,
            "gate_interpretation": contract["gate_interpretation"],
            "selection_performed": False,
            "promotion_performed": False,
        }
    )


def _shadow_progress_payload_from_projection(
    projection: ShadowProjection | None,
    *,
    profile: Mapping[str, Any] | None = None,
    live_projection: object | None = None,
) -> dict[str, Any]:
    """Return the deterministic scientific progress payload (never status/time)."""

    del live_projection
    if projection is None:
        return {
            "schema_version": "shadow_progress_token_payload_v1",
            "profile_sha256": (None if profile is None else profile["_profile_sha256"]),
            "upstream_live_epoch_id": None,
            "live_cursor_sequence_id": 0,
            "live_cursor_sha256": ZERO_HASH,
            "shadow_activated": False,
            "selection_performed": False,
            "promotion_performed": False,
            "automatic_promotion_enabled": False,
            "formal_warning_output": False,
        }
    core = _core(profile) if profile is not None else None
    if core is None:
        raise CalibrationShadowConfig(
            "profile is required for an activated shadow progress token"
        )
    state_hashes = _state_hashes(projection.states, core)
    payload = {
        "schema_version": "shadow_progress_token_payload_v1",
        "profile_sha256": profile["_profile_sha256"],
        "shadow_activated": True,
        "shadow_epoch_id": projection.shadow_epoch_id,
        "shadow_epoch_open": projection.epoch_open,
        "upstream_live_epoch_id": projection.upstream_live_epoch_id,
        "live_cursor_sequence_id": projection.live_cursor_sequence_id,
        "live_cursor_sha256": projection.live_cursor_sha256,
        "shadow_ledger_terminal_sequence_id": (projection.ledger_terminal_sequence_id),
        "method_station_state_sha256": state_hashes,
        "state_aggregate_sha256": _canonical_sha256(state_hashes),
        "last_issue_batch_sha256": projection.last_issue_batch_sha256,
        "outstanding": {
            "target_date": projection.outstanding_target_date,
            "shadow_issue_batch_sha256": (projection.outstanding_issue_batch_sha256),
            "live_issue_seal_entry_sha256": (
                projection.outstanding_live_seal_entry_sha256
            ),
            "engineering_prospective_ordering_candidate": (
                projection.outstanding_prospective_eligible
            ),
        },
        "settled_provenance": {
            target: {
                key: projection.settled[target][key]
                for key in (
                    "shadow_epoch_id",
                    "source_revision_id",
                    "live_outcome_batch_sha256",
                    "live_settlement_entry_sha256",
                    "shadow_issue_batch_sha256",
                    "shadow_outcome_batch_sha256",
                    "engineering_prospective_ordering_candidate",
                )
            }
            for target in sorted(projection.settled)
        },
        "revision_provenance": {
            target: {
                revision: projection.revisions[target][revision]
                for revision in sorted(projection.revisions[target])
            }
            for target in sorted(projection.revisions)
        },
        "backfill_ineligible_provenance": {
            target: projection.backfill_ineligible[target]
            for target in sorted(projection.backfill_ineligible)
        },
        "eligible_daily_replay_records": projection.eligible_daily,
        "sufficient_statistics": projection.sufficient_statistics,
        "joint_available_target_dates": projection.joint_available_target_dates,
        "evaluation_contract": profile["evaluation"],
        "evaluation_contract_sha256": _canonical_sha256(profile["evaluation"]),
        "joint_common_support_only": True,
        "revision_backfill_ineligible_excluded": True,
        "gate_interpretation": profile["evaluation"]["gate_interpretation"],
        "engineering_readiness": _engineering_readiness(projection, profile),
        "selection_performed": False,
        "promotion_performed": False,
        "automatic_promotion_enabled": False,
        "formal_warning_output": False,
        "e2_live_evidence_eligible": False,
    }
    return _json_value(payload)


def _status_payload(
    *,
    status: str,
    profile: Mapping[str, Any],
    projection: ShadowProjection | None,
    live_projection: object | None,
    clock: Clock | None,
) -> dict[str, Any]:
    if status not in SUCCESS_STATUSES:
        raise CalibrationShadowIntegrity("shadow status is outside the whitelist")
    progress = _shadow_progress_payload_from_projection(
        projection, profile=profile, live_projection=live_projection
    )
    observed_cursor = None if live_projection is None else _live_cursor(live_projection)
    return {
        "schema_version": "ootang_prequential_calibration_shadow_status_v1",
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "runner_status": status,
        "generated_at_utc": _clock_text(clock),
        "progress_token_payload": progress,
        "progress_token_sha256": _canonical_sha256(progress),
        "observed_live_cursor_sequence_id": (
            None if observed_cursor is None else observed_cursor[0]
        ),
        "observed_live_cursor_sha256": (
            None if observed_cursor is None else observed_cursor[1]
        ),
        "shadow_ledger_terminal_sequence_id": (
            None if projection is None else projection.ledger_terminal_sequence_id
        ),
        "shadow_ledger_terminal_sha256": (
            None if projection is None else projection.ledger_terminal_sha256
        ),
        "artifact_status": profile["artifact_status"],
        "default_pipeline_member": False,
        "selection_performed": False,
        "promotion_performed": False,
        "automatic_promotion_enabled": False,
        "formal_warning_output": False,
        "e2_live_evidence_eligible": False,
    }


def _write_status(
    *,
    status: str,
    paths: ShadowRuntimePaths,
    profile: Mapping[str, Any],
    projection: ShadowProjection | None,
    live_projection: object | None,
    clock: Clock | None,
) -> CalibrationShadowResult:
    _atomic_write(
        paths.status,
        _status_payload(
            status=status,
            profile=profile,
            projection=projection,
            live_projection=live_projection,
            clock=clock,
        ),
    )
    return CalibrationShadowResult(
        status=status, status_path=paths.status, projection=projection
    )


def _write_blocked_status(
    *,
    paths: ShadowRuntimePaths,
    profile: Mapping[str, Any],
    reason: CalibrationShadowError,
    projection: ShadowProjection | None,
    clock: Clock | None,
) -> None:
    progress: dict[str, Any] | None = None
    if projection is not None:
        try:
            progress = _shadow_progress_payload_from_projection(
                projection, profile=profile, live_projection=None
            )
        except CalibrationShadowError:
            progress = None
    payload = {
        "schema_version": "ootang_prequential_calibration_shadow_status_v1",
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "runner_status": "blocked_integrity",
        "generated_at_utc": _clock_text(clock),
        "normalized_error_type": type(reason).__name__,
        "normalized_reason": str(reason)[:1000],
        "last_verified_progress_token_payload": progress,
        "last_verified_progress_token_sha256": (
            None if progress is None else _canonical_sha256(progress)
        ),
        "status_is_not_scientific_authority": True,
        "artifact_status": profile["artifact_status"],
        "default_pipeline_member": False,
        "selection_performed": False,
        "promotion_performed": False,
        "automatic_promotion_enabled": False,
        "formal_warning_output": False,
        "e2_live_evidence_eligible": False,
    }
    _atomic_write(paths.status, payload)


def _production_live_lock_and_loader(
    profile: Mapping[str, Any],
    *,
    live_runtime_root: Path | None = None,
) -> tuple[Path, Callable[[], object | None]]:
    from monitoring import ootang_prequential_live as live

    live_profile_path = Path(profile["_bound_paths"]["live_profile"])
    try:
        live_profile = live.load_config(live_profile_path)
        live_paths = live.runtime_paths(live_profile, runtime_root=live_runtime_root)
    except live.LiveConfigError as exc:
        raise CalibrationShadowConfig(
            f"bound live profile cannot be loaded: {exc}"
        ) from exc

    def load() -> object | None:
        try:
            prerequisites = live.load_prerequisites(live_profile, live_paths)
            if prerequisites is None or not live_paths.ledger.exists():
                return None
            return live.load_verified_ledger_projection(
                live_profile, live_paths, prerequisites
            )
        except live.LiveConfigError as exc:
            raise CalibrationShadowConfig(str(exc)) from exc
        except (
            live.LivePrerequisiteError,
            live.LiveInputError,
            live.LiveIntegrityError,
        ) as exc:
            raise CalibrationShadowIntegrity(
                f"verified live projection is unavailable: {exc}"
            ) from exc

    return live_paths.lock, load


def shadow_progress_token_payload(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    live_runtime_root: Path | None = None,
    project_root: Path = ROOT,
    projection_loader: ProjectionLoader | None = None,
) -> dict[str, Any]:
    """Load and return the deterministic shadow progress token under both locks.

    This public wrapper intentionally accepts paths rather than caller-supplied
    projections.  It applies the same runner-before-shadow lock order and full
    verified replays as reconciliation.  A test projection is accepted only
    behind the explicit test-override environment gate.
    """

    if projection_loader is not None and os.environ.get(TEST_OVERRIDE_ENV) != "1":
        raise CalibrationShadowConfig(
            f"projection injection requires {TEST_OVERRIDE_ENV}=1"
        )
    profile = load_shadow_profile(config_path, project_root=project_root)
    paths = runtime_paths(profile, runtime_root=runtime_root, project_root=project_root)
    if projection_loader is None:
        upstream_lock_path, load_live = _production_live_lock_and_loader(
            profile, live_runtime_root=live_runtime_root
        )
    else:
        upstream_lock_path = paths.root / "test_upstream_runner.lock"

        def load_live() -> object | None:
            return projection_loader(dict(profile))

    upstream_lock = _acquire_machine_lock(
        upstream_lock_path, label="upstream live runner"
    )
    shadow_lock = None
    try:
        shadow_lock = _acquire_machine_lock(paths.lock, label="calibration shadow")
        live_projection = load_live()
        projection = load_verified_shadow_projection(profile, paths)
        if live_projection is None:
            if projection is not None:
                raise CalibrationShadowIntegrity(
                    "activated shadow lost its verified live prerequisite"
                )
            return _shadow_progress_payload_from_projection(
                None, profile=profile, live_projection=None
            )
        _live_cursor(live_projection)
        if projection is not None:
            if projection.upstream_live_epoch_id == str(live_projection.epoch_id):
                _validate_shadow_live_binding(projection, live_projection)
            elif projection.outstanding_target_date is not None:
                raise CalibrationShadowIntegrity(
                    "upstream epoch changed with outstanding shadow issue"
                )
        return _shadow_progress_payload_from_projection(
            projection, profile=profile, live_projection=live_projection
        )
    finally:
        _release_locks((shadow_lock, upstream_lock))


def _next_unclassified_live_source(
    projection: ShadowProjection, live_projection: object
) -> object | None:
    sources = {
        **_live_backfills(live_projection),
        **_live_settlements(live_projection),
    }
    classified = (
        set(projection.backfill_ineligible)
        | set(projection.settled)
        | set(projection.issues_by_target)
    )
    candidates = [
        event
        for target, event in sources.items()
        if target not in classified
        and int(event.sequence_id) > projection.live_cursor_sequence_id
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda event: int(event.sequence_id))


def _next_revision(
    projection: ShadowProjection, live_projection: object
) -> tuple[int, str, str, dict[str, object]] | None:
    candidates: list[tuple[int, str, str, dict[str, object]]] = []
    for (target, revision_id), rows in _live_revision_groups(live_projection).items():
        maximum_sequence = max(int(event.sequence_id) for event in rows.values())
        if maximum_sequence <= projection.live_cursor_sequence_id:
            continue
        if target not in projection.settled:
            continue
        if revision_id in projection.revisions.get(target, {}):
            known = projection.revisions[target][revision_id]
            batch = _event_payload(rows[STATIONS[0]], name="live.revision.known")[
                "outcome_batch_sha256"
            ]
            if known != batch:
                raise CalibrationShadowIntegrity(
                    "live revision id was reused with changed content"
                )
            continue
        sequence = min(int(event.sequence_id) for event in rows.values())
        candidates.append((sequence, target, revision_id, rows))
    if not candidates:
        return None
    return min(candidates, key=lambda row: row[0])


def reconcile_calibration_shadow(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    live_runtime_root: Path | None = None,
    project_root: Path = ROOT,
    clock: Clock | None = None,
    projection_loader: ProjectionLoader | None = None,
) -> CalibrationShadowResult:
    """Reconcile the machine shadow against one locked verified live projection."""

    if (clock is not None or projection_loader is not None) and os.environ.get(
        TEST_OVERRIDE_ENV
    ) != "1":
        raise CalibrationShadowConfig(
            f"clock/projection injection requires {TEST_OVERRIDE_ENV}=1"
        )
    profile = load_shadow_profile(config_path, project_root=project_root)
    paths = runtime_paths(profile, runtime_root=runtime_root, project_root=project_root)
    if projection_loader is None:
        upstream_lock_path, load_live = _production_live_lock_and_loader(
            profile, live_runtime_root=live_runtime_root
        )
    else:
        upstream_lock_path = paths.root / "test_upstream_runner.lock"

        def load_live() -> object | None:
            return projection_loader(dict(profile))

    upstream_lock = _acquire_machine_lock(
        upstream_lock_path, label="upstream live runner"
    )
    shadow_lock = None
    last_verified_projection: ShadowProjection | None = None
    try:
        shadow_lock = _acquire_machine_lock(paths.lock, label="calibration shadow")
        live_projection = load_live()
        if live_projection is None:
            if paths.ledger.exists():
                raise CalibrationShadowIntegrity(
                    "activated shadow lost its verified live prerequisite"
                )
            return _write_status(
                status="waiting_for_live_prerequisites",
                paths=paths,
                profile=profile,
                projection=None,
                live_projection=None,
                clock=clock,
            )
        _live_cursor(live_projection)
        from monitoring.ootang_calibration_shadow_ledger import (
            AppendOnlyShadowLedger,
            ShadowLedgerError,
        )

        code_sha = _implementation_sha256(profile)
        environment_sha = _environment_sha256()
        wrote = False
        for _ in range(MAX_ACTIONS_PER_RECONCILE):
            projection = load_verified_shadow_projection(profile, paths)
            last_verified_projection = projection
            if projection is None:
                try:
                    ledger = AppendOnlyShadowLedger(paths.ledger)
                    _append_genesis(
                        ledger,
                        profile,
                        live_projection,
                        code_sha=code_sha,
                        environment_sha=environment_sha,
                        previous_terminal_sha256=ZERO_HASH,
                    )
                except ShadowLedgerError as exc:
                    raise CalibrationShadowIntegrity(
                        f"shadow genesis append failed: {exc}"
                    ) from exc
                wrote = True
                continue
            if projection.upstream_live_epoch_id != str(live_projection.epoch_id):
                if projection.outstanding_target_date is not None:
                    raise CalibrationShadowIntegrity(
                        "upstream epoch changed with outstanding shadow issue"
                    )
                try:
                    ledger = AppendOnlyShadowLedger(paths.ledger)
                    if projection.epoch_open:
                        _append_epoch_close(
                            ledger,
                            profile,
                            projection,
                            live_projection,
                            code_sha=code_sha,
                            environment_sha=environment_sha,
                        )
                    else:
                        _append_genesis(
                            ledger,
                            profile,
                            live_projection,
                            code_sha=code_sha,
                            environment_sha=environment_sha,
                            previous_terminal_sha256=(
                                projection.ledger_terminal_sha256
                            ),
                        )
                except ShadowLedgerError as exc:
                    raise CalibrationShadowIntegrity(
                        f"shadow epoch rotation failed: {exc}"
                    ) from exc
                wrote = True
                continue
            if not projection.epoch_open:
                raise CalibrationShadowIntegrity(
                    "closed shadow epoch has no verified upstream rotation"
                )
            _validate_shadow_live_binding(projection, live_projection)
            settlements = _live_settlements(live_projection)
            live_backfills = _live_backfills(live_projection)
            try:
                ledger = AppendOnlyShadowLedger(paths.ledger)
                unclassified = _next_unclassified_live_source(
                    projection, live_projection
                )
                revision = _next_revision(projection, live_projection)
                matching_settlement = None
                current_issue = None
                if projection.outstanding_target_date is not None:
                    target = projection.outstanding_target_date
                    if target in live_backfills:
                        raise CalibrationShadowIntegrity(
                            "sealed shadow issue became an upstream backfill"
                        )
                    matching_settlement = settlements.get(target)
                else:
                    current_issue = _current_live_issue(live_projection)

                pending: list[tuple[int, str, object]] = []
                if unclassified is not None:
                    pending.append(
                        (int(unclassified.sequence_id), "backfill", unclassified)
                    )
                if revision is not None:
                    pending.append((revision[0], "revision", revision))
                if matching_settlement is not None:
                    pending.append(
                        (
                            int(matching_settlement.sequence_id),
                            "settlement",
                            matching_settlement,
                        )
                    )
                if current_issue is not None:
                    issue_sequence = max(
                        int(current_issue[2].sequence_id),
                        projection.live_cursor_sequence_id,
                    )
                    pending.append((issue_sequence, "issue", current_issue))
                if pending:
                    _, action_kind, action = min(pending, key=lambda row: row[0])
                    if action_kind == "settlement":
                        _append_outcome_batch(
                            ledger,
                            profile,
                            projection,
                            live_projection,
                            action,
                            code_sha=code_sha,
                            environment_sha=environment_sha,
                        )
                    elif action_kind == "backfill":
                        _append_backfill_ineligible(
                            ledger,
                            profile,
                            projection,
                            live_projection,
                            action,
                            code_sha=code_sha,
                            environment_sha=environment_sha,
                        )
                    elif action_kind == "revision":
                        _, target, revision_id, rows = action
                        _append_revision_rescores(
                            ledger,
                            profile,
                            projection,
                            live_projection,
                            target=target,
                            revision_id=revision_id,
                            live_rows=rows,
                            code_sha=code_sha,
                            environment_sha=environment_sha,
                        )
                    else:
                        target = action[0]
                        if (
                            target in projection.backfill_ineligible
                            or target in projection.settled
                            or target in projection.issues_by_target
                        ):
                            raise CalibrationShadowIntegrity(
                                "verified live outstanding target was already classified"
                            )
                        _append_issue_batch(
                            ledger,
                            profile,
                            projection,
                            live_projection,
                            code_sha=code_sha,
                            environment_sha=environment_sha,
                        )
                    wrote = True
                    continue

                if projection.outstanding_target_date is not None:
                    target = projection.outstanding_target_date
                    current = _current_live_issue(live_projection)
                    if (
                        current is None
                        or current[0] != target
                        or _event_hash(current[2], name="live.current.seal")
                        != projection.outstanding_live_seal_entry_sha256
                    ):
                        raise CalibrationShadowIntegrity(
                            "outstanding shadow issue disappeared from live chain"
                        )
                    return _write_status(
                        status="waiting_for_live_outcome",
                        paths=paths,
                        profile=profile,
                        projection=projection,
                        live_projection=live_projection,
                        clock=clock,
                    )

            except ShadowLedgerError as exc:
                raise CalibrationShadowIntegrity(
                    f"append-only shadow ledger rejected reconcile: {exc}"
                ) from exc

            return _write_status(
                status="reconciled" if wrote else "waiting_for_live_issue",
                paths=paths,
                profile=profile,
                projection=projection,
                live_projection=live_projection,
                clock=clock,
            )
        projection = load_verified_shadow_projection(profile, paths)
        if projection is None:  # pragma: no cover - genesis is the first action
            raise CalibrationShadowIntegrity(
                "shadow action bound ended without a durable genesis"
            )
        return _write_status(
            status="work_remaining",
            paths=paths,
            profile=profile,
            projection=projection,
            live_projection=live_projection,
            clock=clock,
        )
    except (CalibrationShadowIntegrity, CalibrationShadowConfig) as exc:
        if shadow_lock is not None:
            try:
                _write_blocked_status(
                    paths=paths,
                    profile=profile,
                    reason=exc,
                    projection=last_verified_projection,
                    clock=clock,
                )
            except CalibrationShadowError:
                pass
        raise
    finally:
        _release_locks((shadow_lock, upstream_lock))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile the Ootang prequential calibration shadow"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--live-runtime-root", type=Path)
    args = parser.parse_args(argv)
    try:
        result = reconcile_calibration_shadow(
            config_path=args.config,
            runtime_root=args.runtime_root,
            live_runtime_root=args.live_runtime_root,
        )
    except CalibrationShadowBusy as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except CalibrationShadowError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(result.status_path)
    return 0


__all__ = [
    "CalibrationShadowBusy",
    "CalibrationShadowConfig",
    "CalibrationShadowError",
    "CalibrationShadowIntegrity",
    "CalibrationShadowResult",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_CONFIG_SHA256",
    "ShadowProjection",
    "ShadowRuntimePaths",
    "TEST_OVERRIDE_ENV",
    "load_shadow_profile",
    "load_verified_shadow_projection",
    "main",
    "reconcile_calibration_shadow",
    "runtime_paths",
    "shadow_progress_token_payload",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
