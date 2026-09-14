"""Pre-training checks and immutable implementation receipt; zero updates."""

import subprocess
import sys
import time

import numpy as np
import torch

from .core import (
    ROOT,
    Scaling,
    feature_matrix,
    guard_sources,
    load_npz,
    read_forcing,
    read_labels,
    sha,
    spec,
    utc,
    write_json,
)
from .models import TrajectoryModel
from .independent import numpy_forward


def main():
    cfg = spec()
    out = ROOT / cfg["out"]
    dest = out / "implementation_verification"
    dest.mkdir(parents=True, exist_ok=False)
    source_count = guard_sources()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_sequence_conditional.py",
            "-v",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    (dest / "unit_tests.txt").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("Preflight contracts failed; see unit_tests.txt")
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    comparisons = {}
    parameters = {}
    benchmarks = {}
    for n, end in cfg["stages"].values():
        data = load_npz(
            ROOT / cfg["reuse"] / f"implementation_verification/teacher_{n}.npz"
        )
        forcing, dates = read_forcing(ROOT / cfg["data"], end)
        np.testing.assert_array_equal(dates, data["dates"])
        np.testing.assert_array_equal(forcing, data["forcing"])
        x = feature_matrix(data["mean"], forcing, data, data["y0"])
        np.testing.assert_array_equal(x, data["x"])
        comparisons[f"teacher_{n}_features_max_difference"] = float(
            abs(x - data["x"]).max()
        )
    # Real-feature check reads labels only from the first training prefix.
    data = load_npz(ROOT / cfg["reuse"] / "implementation_verification/teacher_612.npz")
    y = read_labels(ROOT / cfg["data"], 612)
    scale = Scaling(data["x"][:612], y, cfg=cfg)
    for family, pair in cfg["families"].items():
        model = TrajectoryModel(cfg, 0, pair[0])
        parameters[family] = sum(p.numel() for p in model.parameters())
        with torch.no_grad():
            model.head.weight.normal_(0, 0.1)
        inp = scale.tensor(data["x"])
        t = time.monotonic()
        actual = model(inp)
        actual.square().mean().backward()
        benchmarks[family] = dict(
            rows=len(data["x"]),
            forward_backward_seconds=time.monotonic() - t,
            optimizer_updates=0,
        )
        expected = numpy_forward(
            dict(state_dict=model.state_dict(), scaling=scale.state, arm=pair[0]),
            data["x"],
            data["mean"],
        )
        mean = scale.y0 + actual.detach().numpy() * scale.unit
        error = float(abs(expected - mean).max())
        np.testing.assert_allclose(
            expected, mean, atol=cfg["numerical"]["independent_forward_atol_mm"], rtol=0
        )
        comparisons[family + "_real_features_numpy_max_error_mm"] = error
    write_json(
        dest / "numerical_checks.json",
        dict(comparisons=comparisons, parameters=parameters, benchmarks=benchmarks),
    )
    write_json(
        dest / "receipt.json",
        dict(
            status="passed",
            time_utc=utc(),
            source_count=source_count,
            contract_tests=6,
            parameters=parameters,
            comparisons=comparisons,
            benchmarks=benchmarks,
            new_fits=0,
            optimizer_updates=0,
            physical_forwards=0,
            old_fits=0,
            scope="CPU float64 scan vs serial gradients; upstream float32 scan and gradients; independent whole models; causal prefix/reload/zero baseline; three reused teacher caches",
            files={p.name: sha(p) for p in dest.glob("*")},
        ),
    )
    paths = list((ROOT / "code/sequence_conditional").glob("*.py"))
    paths += [ROOT / "tests/test_sequence_conditional.py"] + list(dest.glob("*"))
    write_json(
        out / "implementation_lock.json",
        dict(
            time_utc=utc(),
            frozen_before_training=True,
            files={str(p.relative_to(ROOT)): sha(p) for p in paths},
        ),
    )
    print((dest / "receipt.json").read_text())


if __name__ == "__main__":
    main()
