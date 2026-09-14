"""Extend two frozen teachers and check all origin information boundaries."""

import tempfile
from pathlib import Path

import numpy as np
import torch

from .core import (
    CONFIG,
    SOURCES,
    ROOT,
    OriginModel,
    bank,
    current_teacher,
    draw_schedule,
    guard,
    inputs,
    labels,
    load_npz,
    physical_trajectory,
    predict,
    read_forcing,
    reload,
    scaling,
    setup,
    sha,
    spec,
    targets,
    teacher_for,
    utc,
    write_json,
)


def main():
    cfg = spec()
    setup(cfg)
    root = ROOT / cfg["out"]
    out = root / "preflight"
    out.mkdir(parents=True, exist_ok=False)
    source_count = guard()
    forcing, dates = read_forcing(ROOT / cfg["data"], 1461)
    y = labels(cfg, 612, "preflight_prefix_only")
    old = ROOT / cfg["prior_out"] / "implementation_verification"
    checks, warnings = {}, {}
    for n, end in [(432, 904), (612, 1084)]:
        data, log = physical_trajectory(cfg, n, forcing[:end], y[0])
        prior = load_npz(old / f"teacher_{n}.npz")
        diffs = []
        for k in data:
            if k in prior and data[k].ndim and len(data[k]) == end:
                overlap = len(prior[k])
                diffs.append(float(np.max(abs(data[k][:overlap] - prior[k]))))
        np.testing.assert_array_equal(data["theta"], prior["theta"])
        assert max(diffs) <= cfg["numerical"]["physical_replay_atol_mm"]
        checks[f"teacher_{n}_overlap"] = max(diffs)
        warnings[str(n)] = log
        np.savez_compressed(
            out / f"teacher_{n}.npz", **data, dates=dates[:end], teacher_fit_prefix=n
        )
    teachers = bank(cfg)
    scale = scaling(current_teacher(teachers, 612), y, cfg)
    x = inputs(teachers, y, [432, 500], np.array([[1, 100], [2, 112]]), scale)
    alternate = y.copy()
    alternate[500:] += 10000
    x0 = inputs(teachers, y, [500], np.array([[1, 112]]), scale)
    x1 = inputs(teachers, alternate, [500], np.array([[1, 112]]), scale)
    for a, b in zip(x0, x1):
        torch.testing.assert_close(a, b, rtol=0, atol=0)
    checks["future_observation_input_difference"] = 0
    assert (
        teacher_for(432) == 432
        and teacher_for(611) == 432
        and teacher_for(612) == 612
        and teacher_for(971) == 792
    )
    for n in cfg["origins"]:
        for seed in cfg["seeds"]:
            m, hs = draw_schedule(n, seed, cfg)
            assert (
                np.all(m >= 432) and np.all(m < n) and np.all(m[..., None] + hs - 1 < n)
            )
            assert np.all(hs >= 1) and np.all(hs <= 293)
    checks["all_12_schedules_teacher_target_indices"] = "passed"
    counts = {}
    for arm in cfg["arms"]:
        model = OriginModel(cfg, 0, arm)
        counts[arm] = sum(p.numel() for p in model.parameters())
        np.testing.assert_array_equal(
            predict(model, teachers, y, 612, 905, scale), teachers[612]["mean"][612:905]
        )
        with torch.no_grad():
            model.head.weight.normal_(std=0.1)
            model.head.bias.fill_(0.03)
        v = model(*x)
        one = model(
            *tuple(t[:1, :432] if j in (0, 2, 3) else t[:1] for j, t in enumerate(x))
        )
        torch.testing.assert_close(v[:1], one, atol=1e-12, rtol=0)
        for i in range(2):
            single = list(x)
            single[1] = x[1][:, i : i + 1]
            single[4] = x[4][:, i : i + 1]
            torch.testing.assert_close(
                v[:, i : i + 1], model(*single), atol=1e-12, rtol=0
            )
        changed = list(x)
        changed[0] = x[0].clone()
        changed[0][:, :, 22:] += 1
        dv = float((v - model(*changed)).abs().max().detach())
        if arm == "NO_OBS_ATTN":
            assert dv == 0
        else:
            assert dv > 1e-7
        checks[arm + "_history_observation_effect"] = dv
        changed = list(x)
        changed[1] = x[1].clone()
        changed[1][:, 1:] += 3
        torch.testing.assert_close(v[:, :1], model(*changed)[:, :1], atol=1e-12, rtol=0)
        target = targets(teachers, y, [432, 500], np.array([[1, 100], [2, 112]]), scale)
        loss = (v - target).square().mean() + v.square().mean()
        loss.backward()
        assert all(
            torch.isfinite(p.grad).all()
            for p in model.parameters()
            if p.grad is not None
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.pt"
            torch.save(
                dict(state_dict=model.state_dict(), scaling=scale, seed=0, arm=arm),
                path,
            )
            other, _, _ = reload(path, cfg)
            torch.testing.assert_close(v, other(*x), rtol=0, atol=0)
    checks["zero_fallback_reload_batch_query_and_gradient"] = "all three passed"
    write_json(
        out / "receipt.json",
        dict(
            status="passed",
            time_utc=utc(),
            source_files=source_count,
            physical_forwards=2,
            new_fits=0,
            checks=checks,
            parameters=counts,
            warnings=warnings,
            label_prefix_max=612,
        ),
    )
    files = (
        list((ROOT / "code/transformer_origin").glob("*.py"))
        + [
            CONFIG,
            SOURCES,
            ROOT / "docs/ootang_transformer_origin_plan.v1.0.md",
            ROOT / "docs/ootang_transformer_origin_implementation.v1.0.md",
        ]
        + list(out.glob("*"))
    )
    write_json(
        root / "implementation_lock.json",
        dict(
            time_utc=utc(),
            files={str(p.relative_to(ROOT)): sha(p) for p in sorted(files)},
        ),
    )
    print({"status": "passed", "checks": checks, "parameters": counts}, flush=True)


if __name__ == "__main__":
    main()
