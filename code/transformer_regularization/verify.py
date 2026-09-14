"""Preflight uses only internal training labels and zero optimizer updates."""

import io
import unittest

import numpy as np
import torch

from sequence_conditional.independent import numpy_forward
from .core import (
    CONFIG,
    OLD,
    ROOT,
    TrajectoryModel,
    guard_sources,
    load_npz,
    predict,
    read_forcing,
    read_json,
    read_labels,
    reload_model,
    sha,
    spec,
    training_inputs,
    utc,
    write_json,
)


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    dest = root / "implementation_verification"
    dest.mkdir(parents=True, exist_ok=False)
    record = dict(
        status="running",
        time_utc=utc(),
        formal_new_fits=0,
        optimizer_updates=0,
        physical_forwards=0,
        latest_label_index=611,
    )
    try:
        torch.set_num_threads(1)
        record["source_files"] = guard_sources()
        suite = unittest.defaultTestLoader.discover(
            str(ROOT / "tests"), pattern="test_transformer_regularization.py"
        )
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        (dest / "unit_tests.txt").write_text(stream.getvalue())
        assert result.wasSuccessful(), stream.getvalue()
        record["tests_passed"] = result.testsRun
        for phase, (n, end) in cfg["stages"].items():
            data = load_npz(
                ROOT / cfg["reuse"] / f"implementation_verification/teacher_{n}.npz"
            )
            forcing, dates = read_forcing(ROOT / cfg["data"], end)
            np.testing.assert_array_equal(data["dates"], dates)
            np.testing.assert_array_equal(data["forcing"], forcing)
            assert data["x"].shape == (end, 22) and data["mean"].shape == (end, 4)
            old = read_json(ROOT / cfg["reuse_sequence"] / phase / "scaling.json")
            np.testing.assert_array_equal(old["x_mean"], data["x"][:n].mean(0))
            np.testing.assert_array_equal(
                old["x_std"], np.maximum(data["x"][:n].std(0), 1e-6)
            )
            assert old["training_rows"] == n
        data = load_npz(
            ROOT / cfg["reuse"] / "implementation_verification/teacher_612.npz"
        )
        y = read_labels(ROOT / cfg["data"], 612)
        scale, _, _ = training_inputs(data, y, cfg)
        assert scale.state == read_json(
            ROOT / cfg["reuse_sequence"] / "internal/scaling.json"
        )
        maximum = 0.0
        for s in cfg["neural"]["seeds"]:
            model = TrajectoryModel(cfg, s, OLD)
            p = ROOT / cfg["reuse_sequence"] / "internal" / OLD / f"seed_{s}/e0.pt"
            original = torch.load(p, map_location="cpu", weights_only=True)
            for k, v in model.state_dict().items():
                torch.testing.assert_close(v, original["state_dict"][k], rtol=0, atol=0)
            np.testing.assert_array_equal(
                predict(model, scale, OLD, data["x"], data["mean"]), data["mean"]
            )
            p = p.with_name("e400.pt")
            other, sc, ck = reload_model(p, cfg)
            actual = predict(other, sc, OLD, data["x"], data["mean"])
            expected = numpy_forward(ck, data["x"], data["mean"])
            np.testing.assert_allclose(actual, expected, atol=1e-7, rtol=0)
            maximum = max(maximum, float(np.max(abs(actual - expected))))
        record.update(
            status="passed",
            parameters=sum(p.numel() for p in model.parameters()),
            real_internal_numpy_max_abs_mm=maximum,
            config_sha256=sha(CONFIG),
            caches_checked=3,
            initializations_checked=3,
        )
        files = (
            [CONFIG]
            + list((ROOT / "code/transformer_regularization").glob("*.py"))
            + [ROOT / "tests/test_transformer_regularization.py"]
        )
        write_json(
            root / "implementation_lock.json",
            dict(
                time_utc=utc(), files={str(p.relative_to(ROOT)): sha(p) for p in files}
            ),
        )
    except Exception as exc:
        record.update(status="failed", error=repr(exc))
        raise
    finally:
        write_json(dest / "receipt.json", record)
    print(record)


if __name__ == "__main__":
    main()
