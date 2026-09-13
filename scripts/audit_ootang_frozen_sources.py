"""Verify frozen bytes and record provenance; never import or run a model."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "results/ootang_rolling_v3/20260913"
OUT = ROOT / "results/ootang_rolling_frozen_validation_v1/20260913"
SNAPSHOT = "1b8fc12820eff42e7133e806597489152e794082"
START = "2026-09-13T09:59:11+00:00"
DEADLINE = "2026-09-13T10:59:11+00:00"
MODELS = ("C18_PHYS", "C18_CORE", "C16_FAST_PHYS", "DRIFT1", "B_ANCHOR")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if datetime.now(timezone.utc) >= datetime.fromisoformat(DEADLINE):
        raise RuntimeError("The frozen validation budget has expired")
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "sources_audit.json"
    if target.exists():
        raise FileExistsError("Preserve the previous audit receipt")
    records, runs = [], {}
    for name in (
        "c8_online",
        "c16_fast_feedback_run3",
        "c18_information",
        "c18_analysis",
        "c18_qa",
    ):
        run = BASE / name
        manifest = run / "artifact_manifest.json"
        artifacts = json.loads(manifest.read_text())["files"]
        for rel, expected in artifacts.items():
            path = run / rel
            actual = sha(path) if path.is_file() else None
            records.append(
                dict(
                    run=name,
                    kind="artifact",
                    path=str(path.relative_to(ROOT)),
                    expected=expected,
                    actual=actual,
                    passed=actual == expected,
                )
            )
        sources = run / "sources.json"
        entries = json.loads(sources.read_text())["files"] if sources.exists() else {}
        for rel, expected in entries.items():
            copy = run / "sources" / rel
            path = copy if copy.exists() else ROOT / rel
            actual = sha(path) if path.is_file() else None
            records.append(
                dict(
                    run=name,
                    kind="source",
                    original_path=rel,
                    path=str(path.relative_to(ROOT)),
                    archived_copy=copy.exists(),
                    expected=expected,
                    actual=actual,
                    passed=actual == expected,
                )
            )
        runs[name] = dict(
            manifest=str(manifest.relative_to(ROOT)),
            manifest_sha256=sha(manifest),
            artifacts=len(artifacts),
            sources=len(entries),
        )
    refs = {}
    for name, manifest_run, field in (
        ("c8_verification", "c8_online", None),
        ("c16_verification", "c16_fast_feedback_run3", None),
        ("c18_verification", "c18_information", "run_manifest_sha256"),
        ("c18_statistics_verification", "c18_analysis", "analysis_manifest_sha256"),
    ):
        path = BASE / name / "verification.json"
        data = json.loads(path.read_text())
        if data["passed"] is not True:
            raise ValueError(f"Original verification did not pass: {path}")
        if field and data[field] != runs[manifest_run]["manifest_sha256"]:
            raise ValueError(f"Verification refers to another manifest: {path}")
        refs[name] = dict(
            path=str(path.relative_to(ROOT)),
            sha256=sha(path),
            saved_verification=data,
            rerun=False,
        )
    frozen = {}
    for phase in ("development", "later_exploratory"):
        frozen[phase] = {}
        for name in MODELS:
            path = BASE / "c18_information" / phase / f"{name}.npz"
            frozen[phase][name] = dict(
                path=str(path.relative_to(ROOT)), sha256=sha(path)
            )
    configs = {}
    for version in ("3_7", "3_15", "3_17"):
        path = ROOT / f"config/ootang_rolling_probability.v{version}.json"
        expected = subprocess.check_output(
            ["git", "show", f"{SNAPSHOT}:{path.relative_to(ROOT)}"], cwd=ROOT
        )
        if path.read_bytes() != expected:
            raise ValueError(f"Configuration changed after snapshot: {path}")
        configs[str(path.relative_to(ROOT))] = dict(
            sha256=sha(path), settings=json.loads(expected)
        )
    result = dict(
        passed=all(r["passed"] for r in records),
        checked_utc=datetime.now(timezone.utc).isoformat(),
        start_utc=START,
        deadline_utc=DEADLINE,
        scientific_snapshot=SNAPSHOT,
        plan_commit="584111e",
        auditor_sha256=sha(Path(__file__)),
        runs=runs,
        checks=records,
        frozen_predictions=frozen,
        configurations=configs,
        reused_verifications=refs,
        artifact_checks=sum(r["kind"] == "artifact" for r in records),
        source_checks=sum(r["kind"] == "source" for r in records),
        new_training=0,
        new_physics=0,
        arrays_loaded=0,
        interpretation="Byte/source integrity only; not a new model or effectiveness verification",
    )
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "passed",
                    "checked_utc",
                    "artifact_checks",
                    "source_checks",
                    "new_training",
                    "new_physics",
                    "arrays_loaded",
                )
            }
        )
    )
    if not result["passed"]:
        raise ValueError("Frozen source mismatch; consult the preserved receipt")


if __name__ == "__main__":
    main()
