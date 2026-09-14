"""Teacher provenance and full-state replay; no formal neural training."""

import json
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np
import torch

from .core import (
    ARM,
    CONFIG,
    ROOT,
    TrajectoryModel,
    array_sha,
    guard,
    load_npz,
    physical_trajectory,
    predict,
    read_forcing,
    read_json,
    read_labels,
    reload_model,
    saved_source,
    sha,
    spec,
    training_inputs,
    utc,
    write_json,
)


def prepare():
    cfg = spec()
    root = ROOT / cfg["out"]
    out = root / "implementation_verification"
    out.mkdir(exist_ok=False)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    checks, records, logs = {}, [], {}
    sources = guard()
    forcing, dates = read_forcing(ROOT / cfg["data"], 1461)
    y0 = read_labels(ROOT / cfg["data"], 1)[0]
    calls = 0
    for n, end in zip(cfg["origins"], cfg["ends"]):
        fp = cfg["teacher_fit_prefixes"][str(n)]
        record = read_json(ROOT / cfg["teacher_parameter_records"][str(n)])
        if fp in (432, 612):
            assert record["fit_days"] == fp
            assert record["training_label_sha256"] == array_sha(
                read_labels(ROOT / cfg["data"], fp)
            )
            assert record["training_forcing_sha256"] == array_sha(forcing[:fp])
        elif fp == 792:
            assert record["fit_end"] == dates[fp - 1]
            assert record["physics_version"] == "prefix_792_v1_1"
        else:
            assert record["fit_days"] == fp
        assert fp <= n
        full, log1 = physical_trajectory(cfg, n, forcing[:end], y0)
        prefix, log2 = physical_trajectory(cfg, n, forcing[:n], y0)
        calls += 2
        for key in full:
            if key in ["theta", "y0"]:
                continue
            if full[key].ndim >= 1 and len(full[key]) == end:
                difference = float(np.max(abs(full[key][:n] - prefix[key])))
                checks[f"{n}_{key}_prefix_difference"] = difference
                assert difference <= 1e-8
        if fp in (432, 612, 792):
            np.testing.assert_array_equal(full["theta"], np.array(record["theta"]))
        if n in (612, 792, 1168):
            old = load_npz(
                ROOT
                / "results/ootang_tcn_conditional_v1/20260914/implementation_verification"
                / f"teacher_{n}.npz"
            )
            overlap = min(end, len(old["mean"]))
            np.testing.assert_allclose(
                full["mean"][:overlap], old["mean"][:overlap], atol=1e-8, rtol=0
            )
            np.testing.assert_allclose(
                full["x"][:overlap], old["x"][:overlap], atol=1e-8, rtol=0
            )
        np.savez_compressed(
            out / f"teacher_{n}.npz",
            **full,
            dates=dates[:end],
            teacher_fit_prefix=np.array(fp),
        )
        records.append(
            dict(
                origin=n,
                forecast_end=end,
                fit_prefix=fp,
                theta_sha256=array_sha(full["theta"]),
                source=cfg["teacher_sources"][str(n)],
                record=cfg["teacher_parameter_records"][str(n)],
                historical_optimizer_success=record.get("success", "see source record"),
            )
        )
        logs[str(n)] = log1 + log2
    data = load_npz(out / "teacher_432.npz")
    y = read_labels(ROOT / cfg["data"], 432)
    scale, _, _ = training_inputs(data, y, cfg)
    model = TrajectoryModel(cfg, 0, ARM)
    np.testing.assert_array_equal(
        predict(model, scale, ARM, data["x"], data["mean"]), data["mean"]
    )
    with torch.no_grad():
        model.head.weight.fill_(0.013)
        model.head.bias.fill_(0.02)
    perturbed_forcing = forcing[:612].copy()
    perturbed_forcing[500:, 0] += 2
    altered, log3 = physical_trajectory(cfg, 432, perturbed_forcing, y0)
    calls += 1
    logs["forcing_perturbation"] = log3
    full = predict(model, scale, ARM, data["x"], data["mean"])
    changed = predict(model, scale, ARM, altered["x"], altered["mean"])
    np.testing.assert_array_equal(data["x"][:500], altered["x"][:500])
    checks["nonzero_causal_prefix_difference"] = float(
        np.max(abs(full[:500] - changed[:500]))
    )
    assert checks["nonzero_causal_prefix_difference"] <= 1e-8
    assert np.max(abs(full[500:] - changed[500:])) > 0
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "model.pt"
        torch.save(
            dict(state_dict=model.state_dict(), scaling=scale.state, seed=0, arm=ARM),
            path,
        )
        loaded, sc, _ = reload_model(path, cfg)
        np.testing.assert_array_equal(
            predict(loaded, sc, ARM, data["x"], data["mean"]), full
        )
    initials = 0
    for n in (612, 792, 1168):
        for key in ["L0", "L1"]:
            for seed in range(3):
                initial = torch.load(
                    saved_source(cfg, n, key, seed, 0), weights_only=True
                )
                candidate = TrajectoryModel(cfg, seed, ARM)
                for name, value in candidate.state_dict().items():
                    torch.testing.assert_close(
                        value, initial["state_dict"][name], rtol=0, atol=0
                    )
                initials += 1
    assert calls == cfg["max_physical_forwards"]
    write_json(
        out / "receipt.json",
        dict(
            status="passed",
            time_utc=utc(),
            source_files=sources,
            checks=checks,
            teacher_records=records,
            old_initializations_verified=initials,
            physical_forwards=calls,
            new_neural_fits=0,
            optimizer_updates=0,
            warnings=logs,
            files={p.name: sha(p) for p in sorted(out.glob("*.npz"))},
        ),
    )
    files = [
        CONFIG,
        ROOT / "docs/ootang_transformer_temporal_plan.v1.0.md",
        ROOT / "docs/ootang_transformer_temporal_sources.v1.0.json",
        ROOT / "tests/test_transformer_temporal.py",
    ]
    files += list((ROOT / "code/transformer_temporal").glob("*.py"))
    files += list(out.glob("*"))
    write_json(
        root / "implementation_lock.json",
        dict(
            time_utc=utc(),
            files={str(p.relative_to(ROOT)): sha(p) for p in sorted(files)},
        ),
    )
    print(
        json.dumps(
            dict(
                status="passed",
                sources=sources,
                physical_forwards=calls,
                checks=len(checks),
                old_initializations=initials,
            )
        ),
        flush=True,
    )


if __name__ == "__main__":
    try:
        prepare()
    except Exception:
        cfg = spec()
        write_json(
            ROOT / cfg["out"] / f"preflight_error_{time.time_ns()}.json",
            dict(time=utc(), traceback=traceback.format_exc()),
        )
        raise
