"""Verify branch isolation, amplitude bounds and temporal boundaries before fitting."""

import copy
import os
import traceback
import numpy as np
import torch
from . import core as c
from .numpy_model import forward


def main():
    cfg, o = c.spec(), c.o
    o.setup(cfg)
    sources = c.guard(implementation=False)
    root = c.ROOT / cfg["out"]
    out = root / "preflight"
    out.mkdir(exist_ok=False)
    y = o.labels(cfg, 612, "preflight_prefix_only")
    forcing, _ = o.read_forcing(c.ROOT / cfg["data"], 905)
    teachers = o.bank(cfg)
    sc = c.scaling(cfg, y, forcing[:612], teachers[612])
    checks = []

    def close(name, a, b, tol=1e-10):
        a = a.detach().numpy() if isinstance(a, torch.Tensor) else np.asarray(a)
        b = b.detach().numpy() if isinstance(b, torch.Tensor) else np.asarray(b)
        assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all(), (
            name
        )
        delta = float(np.max(np.abs(a - b), initial=0))
        assert delta <= tol, (name, delta)
        checks.append(dict(name=name, values=int(a.size), max_difference=delta))

    original = c.old.spec()
    for key in [
        "training",
        "optimizer",
        "normalization",
        "calibration",
        "effect",
        "origins",
        "ends",
        "seeds",
        "updates",
        "checkpoints",
        "future_information",
    ]:
        assert cfg[key] == original[key], key
    close(
        "training-only cap",
        sc["hydro_cap_mm"],
        np.maximum(np.sqrt(np.mean((y[30:] - y[:-30]) ** 2, axis=0)), 1),
        0,
    )
    starts = [432, 500, 611]
    values = {
        arm: c.arrays(cfg, teachers, y, forcing[:612], starts, sc, arm, targets=True)
        for arm in cfg["arms"]
    }
    for j in range(4):
        close(
            "matched full inputs and targets " + str(j),
            values["TiDE_SPLIT"][j],
            values["TiDE_BOUND"][j],
            0,
        )
    for arm in cfg["arms"]:
        for i, m in enumerate(starts):
            close(
                f"{arm} history {m}",
                values[arm][0][i],
                ((y[m - 180 : m] - y[m - 1]) / sc["unit"]).T,
                0,
            )
            mature = min(293, 612 - m)
            close(
                f"{arm} first target {m}",
                values[arm][2][i, 0],
                (y[m] - y[m - 1]) / sc["unit"],
                0,
            )
            close(
                f"{arm} maturity {m}",
                values[arm][3][i],
                (np.arange(293) < mature).astype(float),
                0,
            )
            assert (
                torch.count_nonzero(values[arm][1][i, :, 180 + mature + 1 :, :10]) == 0
            )
        changed = y.copy()
        changed[432:] += 1e6
        a = c.arrays(cfg, teachers, y, forcing[:612], [432], sc, arm)
        b = c.arrays(cfg, teachers, changed, forcing[:612], [432], sc, arm)
        close(arm + " future displacement isolation", a[0], b[0], 0)
        poisoned = forcing.copy()
        poisoned[612:] += 1e6
        close(
            arm + " unavailable future driver isolation",
            a[1],
            c.arrays(cfg, teachers, y, poisoned, [432], sc, arm)[1],
            0,
        )
    for n in cfg["origins"]:
        for seed in cfg["seeds"]:
            expected = o.load_npz(
                c.checkpoint_folder(cfg, n, "TiDE_KIN", seed) / "schedule.npz"
            )["origins"]
            close(
                f"n{n}s{seed} exact old schedule", c.schedule(cfg, n, seed), expected, 0
            )
    for seed in cfg["seeds"]:
        a = c.make_model(cfg, seed, "TiDE_SPLIT", sc)
        b = c.make_model(cfg, seed, "TiDE_BOUND", sc)
        old = c.old.Tide(original, seed)
        assert sum(p.numel() for p in a.parameters()) == 123571
        for name, value in a.state_dict().items():
            close("pair init/" + name, value, b.state_dict()[name], 0)
        for name, value in a.main.state_dict().items():
            close("original main init/" + name, value, old.state_dict()[name], 0)
        for arm, model in [("TiDE_SPLIT", a), ("TiDE_BOUND", b)]:
            close(
                arm + " initial output",
                model(*values[arm][:2]),
                np.zeros((3, 294, 4)),
                0,
            )
            loss = c.loss(model, values[arm])
            loss.backward()
            assert torch.isfinite(loss)
        for (name, pa), (_, pb) in zip(a.named_parameters(), b.named_parameters()):
            assert pa.grad is not None and pb.grad is not None
            close("same initial gradient/" + name, pa.grad, pb.grad, 1e-9)
    rng = np.random.default_rng(1701)
    for layer in [
        a.main.temporal.b,
        a.main.temporal.skip,
        a.main.linear,
        a.h_decoder.b,
        a.h_decoder.skip,
    ]:
        with torch.no_grad():
            for p in layer.parameters():
                p.copy_(torch.tensor(rng.normal(0, 0.01, size=p.shape)))
    b.load_state_dict(a.state_dict())
    for arm, model in [("TiDE_SPLIT", a), ("TiDE_BOUND", b)]:
        h, x = values[arm][:2]
        q = model(h, x)
        close(
            arm + " independent nonzero numpy",
            q,
            forward(model.state_dict(), h.numpy(), x.numpy(), arm),
            1e-9,
        )
        close(arm + " h0", q[:, 0], np.zeros((3, 4)), 0)
        close(
            arm + " batch independence",
            q,
            torch.cat([model(h[i : i + 1], x[i : i + 1]) for i in range(3)]),
            1e-9,
        )
        parts = model.parts(h, x)
        hx = x.clone()
        hx[..., 7:10] += 2
        close(arm + " H cannot enter main", parts[0], model.parts(h, hx)[0], 0)
        kx = x.clone()
        kx[..., :7] += 2
        close(
            arm + " common driving and K cannot enter H",
            parts[1],
            model.parts(h, kx)[1],
            0,
        )
        assert (
            float(torch.max(torch.abs(parts[1] - model.parts(h, hx)[1])).detach())
            > 1e-7
        )
        zero = copy.deepcopy(model)
        with torch.no_grad():
            for layer in [zero.h_decoder.b, zero.h_decoder.skip]:
                layer.weight.zero_()
                layer.bias.zero_()
        close(arm + " zero H fallback to own main", zero(h, x), parts[0], 0)
        model.zero_grad()
        c.loss(model, values[arm]).backward()
        assert all(
            p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters()
        )
        path = out / (arm + "_reload.pt")
        torch.save(
            dict(state_dict=model.state_dict(), seed=2, arm=arm, scaling=sc), path
        )
        reloaded, _, _ = c.reload(path)
        close(arm + " reload", q, reloaded(h, x), 0)
    with torch.no_grad():
        b.h_decoder.b.bias[1:] = 1e12
    parts = b.parts(*values["TiDE_BOUND"][:2])
    cap = b.cap_norm[None, None, :]
    assert torch.all(torch.abs(parts[2]) <= cap)
    assert torch.max(torch.abs(parts[1])) > 1e10
    close("bounded extreme positive H", parts[2][:, 1:], cap.expand(3, 293, 4), 0)
    replayed = 0
    for arm in cfg["reuse_arms"]:
        for seed in cfg["seeds"]:
            dest = c.checkpoint_folder(cfg, 612, arm, seed)
            for step in cfg["checkpoints"]:
                model, scale, _ = c.reload(dest / f"e{step}.pt")
                mu = c.predict(cfg, model, teachers, y, forcing, 612, 905, scale, arm)[
                    0
                ]
                close(
                    f"{arm}s{seed}e{step} exact old replay",
                    mu,
                    np.load(dest / f"e{step}_mean.npy"),
                    0,
                )
                replayed += 1
    o.write_json(
        out / "receipt.json",
        dict(
            status="passed",
            source_files=sources,
            checks=len(checks),
            values=sum(v["values"] for v in checks),
            details=checks,
            old_checkpoints_replayed=replayed,
            parameters=123571,
            optimizer_updates=0,
        ),
    )
    o.write_json(
        root / "implementation_lock.json",
        dict(
            time_utc=o.utc(),
            files={
                os.path.relpath(p, root): o.sha(p)
                for p in (c.ROOT / "code/tide_fusion").glob("*.py")
            },
        ),
    )
    o.event(
        root,
        "preflight_passed",
        checks=len(checks),
        values=sum(v["values"] for v in checks),
        optimizer_updates=0,
    )
    print("preflight passed", len(checks), sum(v["values"] for v in checks), "values")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        c.o.write_json(
            c.ROOT / c.spec()["out"] / "preflight_failure.json",
            dict(error=traceback.format_exc()),
        )
        raise
