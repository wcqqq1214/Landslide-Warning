"""Bounded machine poll for the post-manifest epoch settlement chain.

This module is orchestration, not a new scientific or lifecycle authority.  It
reuses the public coordinators that already own validation and durable state,
and writes only a replaceable diagnostic status cache.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_settlement_cycle.v1.json"
PRODUCTION_STAGE_SPECS = (
    (
        "step_dependency_reservation",
        "ootang_epoch_step_dependency_reservation",
        "coordinate_step_dependency_reservation",
    ),
    (
        "step_dependency_overlay",
        "ootang_epoch_step_dependency_overlay",
        "coordinate_epoch_step_dependency_overlay",
    ),
    (
        "source_terminal_aggregate",
        "ootang_epoch_source_terminal_aggregate",
        "coordinate_epoch_source_terminal_aggregate",
    ),
    (
        "manifest_terminal_coverage",
        "ootang_epoch_manifest_terminal_coverage",
        "coordinate_epoch_manifest_terminal_coverage",
    ),
    (
        "source_ingest_derived_reservation",
        "ootang_epoch_source_ingest_derived_reservation",
        "coordinate_source_ingest_derived_reservation",
    ),
    (
        "source_ingest_cross_freeze",
        "ootang_epoch_source_ingest_cross_freeze",
        "coordinate_source_ingest_cross_freeze",
    ),
    (
        "source_derived_workset_overlay",
        "ootang_epoch_source_derived_workset_overlay",
        "coordinate_source_derived_workset_overlay",
    ),
    (
        "source_derived_outcome_dispatch",
        "ootang_epoch_source_derived_outcome_dispatch",
        "coordinate_source_derived_outcome_dispatch",
    ),
    (
        "source_derived_outcome_consumption",
        "ootang_epoch_source_derived_outcome_consumption",
        "coordinate_source_derived_outcome_consumption",
    ),
    (
        "source_derived_dependent_outcome_dispatch",
        "ootang_epoch_source_derived_dependent_outcome_dispatch",
        "coordinate_source_derived_dependent_outcome_dispatch",
    ),
    (
        "source_derived_dependent_outcome_consumption",
        "ootang_epoch_source_derived_dependent_outcome_consumption",
        "coordinate_source_derived_dependent_outcome_consumption",
    ),
    (
        "source_derived_effective_outcome_terminal_coverage",
        "ootang_epoch_source_derived_effective_outcome_terminal_coverage",
        "coordinate_source_derived_effective_outcome_terminal_coverage",
    ),
    (
        "source_derived_source_parent_terminal_aggregate",
        "ootang_epoch_source_derived_source_parent_terminal_aggregate",
        "coordinate_source_derived_source_parent_terminal_aggregate",
    ),
    (
        "source_derived_retained_base_terminal_coverage",
        "ootang_epoch_source_derived_retained_base_terminal_coverage",
        "coordinate_source_derived_retained_base_terminal_coverage",
    ),
    (
        "source_derived_current_effective_workset_terminal_coverage",
        "ootang_epoch_source_derived_current_effective_workset_terminal_coverage",
        "coordinate_source_derived_current_effective_workset_terminal_coverage",
    ),
    (
        "source_derived_bounded_terminal_closure",
        "ootang_epoch_source_derived_bounded_terminal_closure",
        "coordinate_source_derived_bounded_terminal_closure",
    ),
)
EXPECTED_STAGE_ORDER = tuple(spec[0] for spec in PRODUCTION_STAGE_SPECS)
EXPECTED_CLAIMS = {
    "machine_only": True,
    "cache_authority": False,
    "default_pipeline_member": False,
    "old_epoch_drained": False,
    "lifecycle_authority": False,
    "active_switch_performed": False,
    "e2_live_evidence_eligible": False,
    "formal_warning_output": False,
}


class EpochSettlementCycleError(RuntimeError):
    """Base error for the post-manifest settlement runner."""


class EpochSettlementCycleConfigError(EpochSettlementCycleError):
    """The small orchestration profile is invalid."""


class EpochSettlementCycleStageError(EpochSettlementCycleError):
    """An upstream coordinator failed its own contract."""


@dataclass(frozen=True)
class SettlementStage:
    name: str
    run: Callable[[], object]


@dataclass(frozen=True)
class EpochSettlementCycleResult:
    status: str
    reason: str
    status_path: Path
    passes: int
    progress_token_sha256: str
    current_source_derived_bounded_terminal_closure: bool = False
    old_epoch_drained: bool = False
    lifecycle_authority: bool = False


def _canonical_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise EpochSettlementCycleStageError(
            "stage result is not canonical finite JSON"
        ) from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_mapping(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EpochSettlementCycleConfigError(f"{name} keys changed")
    return value


def load_epoch_settlement_cycle_profile(
    path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    """Load the deliberately small, non-authoritative cycle profile."""

    try:
        raw = path.read_bytes()
        profile = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise EpochSettlementCycleConfigError("cycle profile cannot be read") from exc
    profile = _exact_mapping(
        profile,
        {
            "schema_version",
            "profile_id",
            "artifact_status",
            "runtime",
            "cycle",
            "claims",
        },
        name="cycle profile",
    )
    expected_scalars = {
        "schema_version": "ootang_epoch_settlement_cycle_profile_v1",
        "profile_id": "ootang-epoch-settlement-cycle-v1",
        "artifact_status": (
            "machine_only_engineering_not_drain_or_activation_authority"
        ),
    }
    for key, expected in expected_scalars.items():
        if profile[key] != expected:
            raise EpochSettlementCycleConfigError(f"cycle profile {key} changed")
    runtime = _exact_mapping(profile["runtime"], {"status"}, name="runtime")
    if runtime["status"] != (
        "runtime/ootang_epoch_registry_v1/settlement_cycle_v1/status.json"
    ):
        raise EpochSettlementCycleConfigError("runtime.status changed")
    cycle = _exact_mapping(profile["cycle"], {"passes_per_poll"}, name="cycle")
    if (
        not isinstance(cycle["passes_per_poll"], int)
        or isinstance(cycle["passes_per_poll"], bool)
        or cycle["passes_per_poll"] != 1
    ):
        raise EpochSettlementCycleConfigError("cycle bounds changed")
    claims = _exact_mapping(profile["claims"], set(EXPECTED_CLAIMS), name="claims")
    if any(claims[key] is not expected for key, expected in EXPECTED_CLAIMS.items()):
        raise EpochSettlementCycleConfigError("cycle authority boundary changed")
    profile["_profile_sha256"] = hashlib.sha256(raw).hexdigest()
    return profile


def _production_stages() -> tuple[SettlementStage, ...]:
    stages = []
    try:
        for name, module_name, function_name in PRODUCTION_STAGE_SPECS:
            module = importlib.import_module(f"monitoring.{module_name}")
            run = getattr(module, function_name)
            if not callable(run):
                raise TypeError(f"{function_name} is not callable")
            stages.append(SettlementStage(name, run))
    except (ImportError, AttributeError, TypeError) as exc:
        raise EpochSettlementCycleStageError(
            f"settlement coordinator cannot be loaded: {exc}"
        ) from exc
    return tuple(stages)


def _json_value(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise EpochSettlementCycleStageError(
        f"unsupported stage-result value {type(value).__name__}"
    )


def _stage_snapshot(stage: SettlementStage, result: object) -> dict[str, object]:
    if not is_dataclass(result) or isinstance(result, type):
        raise EpochSettlementCycleStageError(
            f"{stage.name} returned a non-dataclass result"
        )
    payload = {
        field.name: _json_value(getattr(result, field.name)) for field in fields(result)
    }
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        raise EpochSettlementCycleStageError(f"{stage.name} returned no stable status")
    return {
        "stage": stage.name,
        "status": status,
        "result_sha256": _sha256(payload),
        "bounded_terminal_closure": bool(
            payload.get("current_source_derived_bounded_terminal_closure", False)
        ),
    }


def _run_stage(stage: SettlementStage) -> dict[str, object]:
    return _stage_snapshot(stage, stage.run())


def _write_status(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical_bytes(dict(payload)))
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise EpochSettlementCycleStageError("cycle status cannot be written") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EpochSettlementCycleStageError("cycle clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def coordinate_epoch_settlement_cycle(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    project_root: Path = ROOT,
    stages: Sequence[SettlementStage] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> EpochSettlementCycleResult:
    """Run one bounded topological pass over existing settlement authorities."""

    profile = load_epoch_settlement_cycle_profile(config_path)
    selected = tuple(stages) if stages is not None else _production_stages()
    if tuple(stage.name for stage in selected) != EXPECTED_STAGE_ORDER:
        raise EpochSettlementCycleConfigError("settlement stage order changed")
    status_path = project_root / profile["runtime"]["status"]
    snapshots = [_run_stage(stage) for stage in selected]
    final_token = _sha256(snapshots)
    closure = bool(snapshots[-1]["bounded_terminal_closure"])
    if closure:
        final_status = "bounded_terminal_closure_reached"
        reason = "existing bounded terminal-closure authority is current"
    else:
        final_status = "settlement_poll_complete"
        reason = "one bounded pass completed; the next machine poll may continue"

    payload = {
        "schema_version": "ootang_epoch_settlement_cycle_status_v1",
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "cycle_status": final_status,
        "reason": reason,
        "recorded_at_utc": _utc_text(clock()),
        "passes": profile["cycle"]["passes_per_poll"],
        "stage_order": list(EXPECTED_STAGE_ORDER),
        "progress_token_sha256": final_token,
        "stage_snapshots": snapshots,
        "current_source_derived_bounded_terminal_closure": closure,
        **profile["claims"],
    }
    _write_status(status_path, payload)
    return EpochSettlementCycleResult(
        status=final_status,
        reason=reason,
        status_path=status_path,
        passes=profile["cycle"]["passes_per_poll"],
        progress_token_sha256=final_token,
        current_source_derived_bounded_terminal_closure=closure,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = coordinate_epoch_settlement_cycle(config_path=args.config)
    except EpochSettlementCycleError as exc:
        print(
            json.dumps(
                {"status": "blocked_integrity", "reason": str(exc)}, sort_keys=True
            )
        )
        return 1
    except Exception as exc:
        busy = type(exc).__name__.endswith("BusyError")
        print(
            json.dumps(
                {
                    "status": "busy" if busy else "blocked_integrity",
                    "reason": f"{type(exc).__name__}:{exc}",
                },
                sort_keys=True,
            )
        )
        return 3 if busy else 1
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "status_path": str(result.status_path),
                "passes": result.passes,
                "progress_token_sha256": result.progress_token_sha256,
                "current_source_derived_bounded_terminal_closure": (
                    result.current_source_derived_bounded_terminal_closure
                ),
                "old_epoch_drained": False,
                "lifecycle_authority": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
