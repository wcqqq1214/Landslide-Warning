"""Close a verified finite experiment and inventory only this task's artifacts."""

from datetime import datetime, timezone
import json

from .core import (
    CONFIG,
    SOURCES,
    ROOT,
    check_deadline,
    guard_sources,
    read_json,
    sha,
    spec,
    utc,
    verify_lock,
    write_json,
)


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    figures = ROOT / cfg["figures"]
    check_deadline(cfg)
    source_count = guard_sources()
    preflight = read_json(root / "implementation_verification/receipt.json")
    numerical = read_json(root / "verification_v1/receipt.json")
    report = read_json(root / "analysis/report_qa.json")
    geometry = read_json(figures / "v1/delivery_qa.json")
    visual = read_json(figures / "v1/visual_qa.json")
    for result in (preflight, numerical, report, geometry, visual):
        assert result["status"] == "passed"
    for phase in ("development", "final_exploratory"):
        for name in ("mean_lock.json", "distribution_lock.json", "scoring_lock.json"):
            verify_lock(root / phase / name)
    verify_lock(root / "selection_lock.json")
    verify_lock(figures / "v1/manifest.json")
    assert (
        sha(ROOT / "docs/ootang_transformer_calibration_results.v1.0.md")
        == report["report_sha256"]
    )
    assert all(
        sha(ROOT / p) == h
        for p, h in read_json(root / "implementation_lock.json")["files"].items()
    )
    external = read_json(SOURCES)["external_display_reference"]
    assert sha(ROOT / external["path"]) == external["sha256"]
    events = [
        json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()
    ]
    assert len(events) == 13 and not any(
        e["event"] in ("run_error", "fit_started") for e in events
    )
    phase_times = {}
    for phase in ("development", "final_exploratory"):
        start = next(
            e["time_utc"]
            for e in events
            if e["event"] == "phase_started" and e["phase"] == phase
        )
        end = next(
            e["time_utc"]
            for e in events
            if e["event"] == "phase_completed" and e["phase"] == phase
        )
        phase_times[phase] = dict(
            start_utc=start,
            end_utc=end,
            seconds=(
                datetime.fromisoformat(end) - datetime.fromisoformat(start)
            ).total_seconds(),
        )
    now = datetime.now(timezone.utc)
    elapsed = (now - datetime.fromisoformat(cfg["start_utc"])).total_seconds()
    assert now < datetime.fromisoformat(cfg["deadline_utc"])
    receipt = dict(
        status="complete",
        prepared_at_utc=now.isoformat(),
        local_timezone="Asia/Shanghai",
        budget=dict(
            start_utc=cfg["start_utc"],
            deadline_utc=cfg["deadline_utc"],
            limit_minutes=120,
            elapsed_minutes_until_verified_delivery=elapsed / 60,
            within_budget=True,
            unused_budget_not_reused=True,
        ),
        phase_times=phase_times,
        branch=cfg["branch"],
        commits=dict(
            plan="a82c912",
            implementation="c314401",
            experiment="1778098",
            figures="bd84c41",
        ),
        new_fits=0,
        optimizer_updates=0,
        ridge_refits=0,
        bplus_refits=0,
        physical_forwards=0,
        source_files_checked=source_count,
        checkpoints_reloaded=numerical["model_checkpoints"],
        distributions=36,
        summary_rows=36,
        point_rows=144,
        seed_summary_rows=54,
        daily_prediction_rows=48168,
        numerical_values_checked=numerical["numerical_values_checked"],
        maximum_numerical_difference=numerical["max_abs_difference"],
        figures=geometry["figures"],
        panels=geometry["panels"],
        figure_numeric_values=geometry["curve_and_band_values_checked"],
        report_tables=report["tables"],
        report_numeric_cells=report["report_numeric_cells"],
        selection=read_json(root / "selection.json"),
        outcome=read_json(root / "analysis/outcome.json"),
        execution_validation="passed",
        stable_joint_scientific_goal="not achieved",
        acceptance="user/mentor acceptance not claimed",
        warnings_preserved="NumPy matrix warnings; finite explicit scalar-sum verification passed; underlying cause not determined",
        display_checks="7 figures fully inspected; static checker limitations and metadata/marker-audit fixes recorded separately",
        protocol="given observed future rain/RWL, no displacement feedback; final period exploratory",
        future_work="no automatic lambda search, RL, new architecture, new time-origin fit or budget continuation",
        unrelated_user_file_unchanged=external["path"],
        pdf=False,
        push=False,
    )
    write_json(root / "final_receipt.json", receipt)
    paths = [
        CONFIG,
        SOURCES,
        ROOT / cfg["plan"],
        ROOT / "docs/ootang_transformer_calibration_results.v1.0.md",
        ROOT / "docs/ootang_transformer_calibration_validation.v1.0.md",
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "docs/README.md",
        ROOT / "docs/progress.md",
        ROOT / "tests/test_transformer_calibration.py",
    ]
    paths += list((ROOT / "code/transformer_calibration").glob("*.py"))
    paths += [
        p
        for d in (root, figures)
        for p in d.rglob("*")
        if p.is_file()
        and p.name != "final_manifest.json"
        and "__pycache__" not in str(p)
    ]
    unique = sorted(set(paths))
    manifest = dict(
        time_utc=utc(),
        count=len(unique),
        files={str(p.relative_to(ROOT)): sha(p) for p in unique},
        excluded="self manifest, pre-existing untracked user rules file, interpreter caches; external reference hash retained in frozen sources",
    )
    write_json(root / "final_manifest.json", manifest)
    assert all(sha(ROOT / p) == h for p, h in manifest["files"].items())
    print(
        json.dumps(
            dict(
                status=receipt["status"],
                files=len(unique),
                elapsed_minutes=elapsed / 60,
                deadline=cfg["deadline_utc"],
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
