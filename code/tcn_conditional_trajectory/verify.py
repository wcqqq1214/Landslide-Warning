"""Pre-training contracts and post-training independent artifact verification."""

import argparse
import json

import numpy as np
import torch

from .core import (
    ARMS,
    ROOT,
    Scaling,
    TrajectoryTCN,
    array_sha,
    guard_sources,
    load_npz,
    physical_trajectory,
    predict,
    read_forcing,
    read_json,
    read_labels,
    sha,
    spec,
    utc,
    write_json,
)


def prepare():
    cfg = spec()
    torch.set_num_threads(1)
    count = guard_sources()
    out = ROOT / cfg["out"] / "implementation_verification"
    out.mkdir(parents=True, exist_ok=False)
    forcing, dates = read_forcing(ROOT / cfg["data"], 1461)
    y = read_labels(ROOT / cfg["data"], 612)
    checks, warnings, calls = {}, {}, 0
    source_rows = []
    for phase, (n, end) in cfg["stages"].items():
        data, log = physical_trajectory(cfg, n, forcing[:end], y[0])
        short, short_log = physical_trajectory(cfg, n, forcing[:n], y[0])
        calls += 2
        warnings[phase] = log + short_log
        checks[phase + "_prefix_mm"] = float(
            np.max(abs(data["mean"][:n] - short["mean"]))
        )
        for key in (
            "coordinates",
            "plastic",
            "basal_reaction",
            "contact",
            "bulk_reaction",
            "background",
        ):
            checks[phase + "_" + key] = float(np.max(abs(data[key][:n] - short[key])))
        if n < 1168:
            old = load_npz(ROOT / cfg["teacher_sources"][str(n)])
            record = read_json(ROOT / cfg["teacher_parameter_records"][str(n)])
            np.testing.assert_array_equal(data["theta"], record["theta"])
            if n == 612:
                assert record["fit_days"] == n
                assert record["training_label_sha256"] == array_sha(y)
                assert record["training_forcing_sha256"] == array_sha(forcing[:n])
            else:
                assert record["fit_end"] == dates[n - 1]
                assert record["physics_version"] == "prefix_792_v1_1"
            prior_mean = old["mean"]
        else:
            source = ROOT / cfg["runtime"] / "section2d_v4/results/curves.npz"
            with np.load(source) as a:
                prior_mean = a["predicted"].copy()
            protocol = read_json(source.parent / "protocol.json")
            assert protocol["fit_days"] == n
        checks[phase + "_original_mm"] = float(np.max(abs(data["mean"] - prior_mean)))
        source_rows.append(
            dict(
                phase=phase,
                prefix=n,
                last_fit_date=str(dates[n - 1]),
                source=cfg["teacher_sources"][str(n)],
                source_sha256=sha(ROOT / cfg["teacher_sources"][str(n)]),
                theta_sha256=array_sha(data["theta"]),
            )
        )
        np.savez_compressed(out / f"teacher_{n}.npz", dates=dates[:end], **data)
    internal = load_npz(out / "teacher_612.npz")
    scale = Scaling(internal["x"][:612], y, cfg=cfg)
    model = TrajectoryTCN(cfg, 0)
    zero_d = predict(model, scale, ARMS[0], internal["x"], internal["mean"])
    zero_r = predict(model, scale, ARMS[1], internal["x"], internal["mean"])
    np.testing.assert_array_equal(zero_d, np.broadcast_to(y[0], zero_d.shape))
    np.testing.assert_array_equal(zero_r, internal["mean"])
    changed_forcing = forcing[:792].copy()
    changed_forcing[650:, 0] += 2.0
    changed_forcing[650:, 1] += 0.25
    altered, log = physical_trajectory(cfg, 612, changed_forcing, y[0])
    calls += 1
    warnings["forcing_perturbation"] = log
    np.testing.assert_array_equal(internal["x"][:650], altered["x"][:650])
    with torch.no_grad():
        model.head.weight.fill_(0.01)
        model.head.bias.fill_(0.02)
    full = predict(model, scale, ARMS[1], internal["x"], internal["mean"])
    perturbed = predict(model, scale, ARMS[1], altered["x"], altered["mean"])
    checks["causal_prefix_mm"] = float(np.max(abs(full[:650] - perturbed[:650])))
    assert np.max(abs(full[650:] - perturbed[650:])) > 0
    chunks = []
    for start in range(0, 792, 97):
        a, b = max(0, start - 60), min(start + 97, 792)
        part = predict(model, scale, ARMS[1], internal["x"][a:b], internal["mean"][a:b])
        chunks.append(part[start - a :])
    checks["chunk_mm"] = float(np.max(abs(full - np.concatenate(chunks))))
    assert max(checks.values()) <= cfg["numerical"]["physical_replay_atol_mm"], checks
    files = {p.name: sha(p) for p in sorted(out.glob("*.npz"))}
    receipt = dict(
        status="passed",
        time_utc=utc(),
        source_count=count,
        comparisons=checks,
        teachers=source_rows,
        files=files,
        formal_fits=0,
        optimizer_updates=0,
        physical_forward_calls=calls,
        neural_parameters=sum(p.numel() for p in model.parameters()),
        warnings=warnings,
    )
    write_json(out / "receipt.json", receipt)
    print(
        json.dumps(
            {
                k: v
                for k, v in receipt.items()
                if k not in ("warnings", "teachers", "files")
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare"])
    parser.parse_args()
    prepare()
