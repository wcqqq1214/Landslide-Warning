"""Verify a sealed v1.9 run without changing its sources, results, or tolerances.

The original verifier receives R.npz's extra background field and constructs
49 checks, while the pre-save native report contains 48. This adapter requires
the extra check to pass, retains it separately, and compares the other 48 exactly.
The original verifier and all its other checks execute unchanged.
"""

import argparse
import contextlib
import json
from pathlib import Path
import shutil
import time
import traceback

import numpy as np

from physics_guided.training import setup
from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    index_artifacts,
    read_json,
    seal,
    sha,
)
from . import verify as frozen
from .workflow import now, replay_audit, specification

NATIVE_FIELDS = {
    "previous",
    "current",
    "loads",
    "lcp",
    "masks",
    "bad",
    "coordinates",
    "plastic",
    "contact",
    "bulk_reaction",
    "basal_reaction",
}
EXTRA_CHECK = "daily_recorded_background"


class SavedReplayAudit:
    def __init__(self, spec):
        self.order = [(h, seed) for h in spec["prefixes"] for seed in spec["seeds"]]
        self.reconciliations = []

    def __call__(self, bundle, prediction, recorded):
        if set(recorded) != NATIVE_FIELDS | {"mean", "background"}:
            raise ValueError("Unexpected saved replay fields")
        if len(self.reconciliations) >= len(self.order):
            raise ValueError("Unexpected extra replay audit")
        h, seed = self.order[len(self.reconciliations)]
        shape = (h + 180, 4)
        for name in ("mean", "background"):
            value = recorded[name]
            if value.shape != shape or not np.isfinite(value).all():
                raise ValueError(f"Invalid saved replay {name}")
        expected, report = replay_audit(bundle, prediction, recorded)
        if report["days"] != h + 180 or len(report["checks"]) != 49:
            raise ValueError("Unexpected replay audit schema or ordering")
        extra = report["checks"][EXTRA_CHECK]
        if not report["passed"] or not extra["passed"]:
            raise ArithmeticError("Saved R fails the unchanged mechanical checks")
        self.reconciliations.append(
            dict(
                prefix=h,
                seed=seed,
                checks_before_save=48,
                checks_after_load=49,
                retained_additional_check={EXTRA_CHECK: extra},
            )
        )
        # Only this known, already-checked addition is removed from the exact
        # legacy report comparison. No numerical field or threshold is changed.
        del report["checks"][EXTRA_CHECK]
        return expected, report


@contextlib.contextmanager
def saved_replay_contract(adapter):
    # A scoped in-memory binding preserves the frozen verifier's on-disk hash.
    # It also avoids copying or bypassing its checkpoint/probability checks.
    original = frozen.replay_audit
    if original is not replay_audit:
        raise RuntimeError("Frozen replay audit already replaced")
    frozen.replay_audit = adapter
    try:
        yield
    finally:
        frozen.replay_audit = original


def verify_archive(out):
    failure = read_json(out / "failed.json")
    if failure["error"] != "ValueError('R mechanical audit differs or fails')":
        raise ValueError("This adapter only handles the recorded report-schema failure")
    if (
        read_json(out / "launcher.json")["exitcode"] != 1
        or (out / "completed.json").exists()
    ):
        raise ValueError("Original terminal run status differs")
    setup(0)
    adapter = SavedReplayAudit(specification())
    with saved_replay_contract(adapter):
        verification = frozen.verify(out)
    if len(adapter.reconciliations) != 9:
        raise ValueError("Missing saved replay audits")
    check_index(out)
    return dict(
        passed=True,
        original_process_exitcode=1,
        original_failure_preserved=True,
        new_optimizer_updates=0,
        new_native_integrations=0,
        verification=verification,
        schema_reconciliations=adapter.reconciliations,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--output-id", help="Write a separate exclusive verification archive"
    )
    args = parser.parse_args()
    for name in (args.run_id, args.output_id):
        if name is not None and (not name or Path(name).name != name):
            raise ValueError("Unsafe run or output id")
    root = ROOT / "results/ootang_bplus_v1_9"
    source = root / args.run_id
    if args.output_id is None:
        print(json.dumps(verify_archive(source), indent=2))
        return
    out = root / args.output_id
    out.mkdir(exist_ok=False)
    sources = {
        name: sha(ROOT / name)
        for name in (
            "code/physics_guided_state_pinn/postcheck.py",
            "tests/test_physics_guided_state_pinn_postcheck.py",
        )
    }
    for name in sources:
        destination = out / "sources" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    seal(
        out / "manifest.json",
        dict(
            kind="read_only_postrun_verification",
            source_run=str(source.relative_to(ROOT)),
            source_index_sha256=sha(source / "artifact_manifest.json"),
            source_failure_sha256=sha(source / "failed.json"),
            sources=sources,
            created_utc=now(),
        ),
    )
    started = time.monotonic()
    try:
        with (out / "run.log").open("x", buffering=1) as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                try:
                    result = verify_archive(source)
                    check_hashes(sources)
                    seal(out / "verification.json", result)
                    seal(
                        out / "completed.json",
                        dict(
                            verification_complete=True,
                            completed_utc=now(),
                            elapsed_seconds=time.monotonic() - started,
                            new_optimizer_updates=0,
                            new_native_integrations=0,
                        ),
                    )
                except BaseException as error:
                    traceback.print_exc()
                    seal(out / "failed.json", dict(error=repr(error), failed_utc=now()))
                    raise
    finally:
        index_artifacts(out)
    print(
        json.dumps(
            {"output": str(out), "passed": result["passed"], **result["verification"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
