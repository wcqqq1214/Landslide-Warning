"""Run one machine-scheduled pass over the post-manifest settlement chain.

The imported coordinators own validation and durable state. This adapter only
orders one call to each coordinator and atomically replaces a diagnostic cache.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import (  # noqa: E402
    ootang_epoch_bounded_drain_completion as bounded_drain_completion,
    ootang_epoch_manifest_terminal_coverage as manifest_terminal,
    ootang_epoch_source_derived_bounded_terminal_closure as bounded_closure,
    ootang_epoch_source_derived_current_effective_workset_terminal_coverage as current_coverage,
    ootang_epoch_source_derived_dependent_outcome_consumption as dependent_consume,
    ootang_epoch_source_derived_dependent_outcome_dispatch as dependent_dispatch,
    ootang_epoch_source_derived_effective_outcome_terminal_coverage as effective_coverage,
    ootang_epoch_source_derived_outcome_consumption as outcome_consume,
    ootang_epoch_source_derived_outcome_dispatch as outcome_dispatch,
    ootang_epoch_source_derived_retained_base_terminal_coverage as retained_coverage,
    ootang_epoch_source_derived_source_parent_terminal_aggregate as parent_aggregate,
    ootang_epoch_source_derived_workset_overlay as workset_overlay,
    ootang_epoch_source_ingest_cross_freeze as cross_freeze,
    ootang_epoch_source_ingest_derived_reservation as derived_reserve,
    ootang_epoch_source_terminal_aggregate as source_terminal,
    ootang_epoch_step_dependency_overlay as dependency_overlay,
    ootang_epoch_step_dependency_reservation as dependency_reserve,
)


STATUS_PATH = ROOT / "runtime/ootang_epoch_registry_v1/settlement_cycle_v1/status.json"


@dataclass(frozen=True)
class SettlementStage:
    name: str
    run: Callable[[], object]


PRODUCTION_STAGES = (
    SettlementStage(
        "step_dependency_reservation",
        dependency_reserve.coordinate_step_dependency_reservation,
    ),
    SettlementStage(
        "step_dependency_overlay",
        dependency_overlay.coordinate_epoch_step_dependency_overlay,
    ),
    SettlementStage(
        "source_terminal_aggregate",
        source_terminal.coordinate_epoch_source_terminal_aggregate,
    ),
    SettlementStage(
        "manifest_terminal_coverage",
        manifest_terminal.coordinate_epoch_manifest_terminal_coverage,
    ),
    SettlementStage(
        "source_ingest_derived_reservation",
        derived_reserve.coordinate_source_ingest_derived_reservation,
    ),
    SettlementStage(
        "source_ingest_cross_freeze",
        cross_freeze.coordinate_source_ingest_cross_freeze,
    ),
    SettlementStage(
        "source_derived_workset_overlay",
        workset_overlay.coordinate_source_derived_workset_overlay,
    ),
    SettlementStage(
        "source_derived_outcome_dispatch",
        outcome_dispatch.coordinate_source_derived_outcome_dispatch,
    ),
    SettlementStage(
        "source_derived_outcome_consumption",
        outcome_consume.coordinate_source_derived_outcome_consumption,
    ),
    SettlementStage(
        "source_derived_dependent_outcome_dispatch",
        dependent_dispatch.coordinate_source_derived_dependent_outcome_dispatch,
    ),
    SettlementStage(
        "source_derived_dependent_outcome_consumption",
        dependent_consume.coordinate_source_derived_dependent_outcome_consumption,
    ),
    SettlementStage(
        "source_derived_effective_outcome_terminal_coverage",
        effective_coverage.coordinate_source_derived_effective_outcome_terminal_coverage,
    ),
    SettlementStage(
        "source_derived_source_parent_terminal_aggregate",
        parent_aggregate.coordinate_source_derived_source_parent_terminal_aggregate,
    ),
    SettlementStage(
        "source_derived_retained_base_terminal_coverage",
        retained_coverage.coordinate_source_derived_retained_base_terminal_coverage,
    ),
    SettlementStage(
        "source_derived_current_effective_workset_terminal_coverage",
        current_coverage.coordinate_source_derived_current_effective_workset_terminal_coverage,
    ),
    SettlementStage(
        "source_derived_bounded_terminal_closure",
        bounded_closure.coordinate_source_derived_bounded_terminal_closure,
    ),
    SettlementStage(
        "bounded_drain_completion",
        bounded_drain_completion.coordinate_epoch_bounded_drain_completion,
    ),
)


@dataclass(frozen=True)
class EpochSettlementCycleResult:
    status: str
    reason: str
    status_path: Path
    current_source_derived_bounded_terminal_closure: bool
    bounded_official_workset_drained: bool


def _write_status(path: Path, payload: dict[str, object]) -> None:
    """Atomically replace the cross-process diagnostic cache."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def coordinate_epoch_settlement_cycle(
    *,
    status_path: Path = STATUS_PATH,
    stages: Sequence[SettlementStage] = PRODUCTION_STAGES,
) -> EpochSettlementCycleResult:
    """Run each existing settlement coordinator once in dependency order."""

    snapshots: list[dict[str, object]] = []
    closure = False
    drained = False
    for stage in stages:
        stage_result = stage.run()
        snapshots.append(
            {"stage": stage.name, "status": getattr(stage_result, "status")}
        )
        closure = closure or bool(
            getattr(
                stage_result,
                "current_source_derived_bounded_terminal_closure",
                False,
            )
        )
        drained = drained or bool(
            getattr(stage_result, "bounded_official_workset_drained", False)
        )

    closure = closure or drained
    if drained:
        status = "bounded_official_workset_drained"
        reason = "the V2 official-machine reserved workset is durably drained"
    elif closure:
        status = "bounded_terminal_closure_reached"
        reason = "existing bounded terminal-closure authority is current"
    else:
        status = "settlement_poll_complete"
        reason = "one bounded pass completed; the next machine poll may continue"

    _write_status(
        status_path,
        {
            "cache_authority": False,
            "cycle_status": status,
            "reason": reason,
            "recorded_at_utc": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "stages": snapshots,
            "current_source_derived_bounded_terminal_closure": closure,
            "bounded_official_workset_drained": drained,
        },
    )
    return EpochSettlementCycleResult(status, reason, status_path, closure, drained)


def main() -> int:
    try:
        result = coordinate_epoch_settlement_cycle()
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
                "current_source_derived_bounded_terminal_closure": (
                    result.current_source_derived_bounded_terminal_closure
                ),
                "bounded_official_workset_drained": (
                    result.bounded_official_workset_drained
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
