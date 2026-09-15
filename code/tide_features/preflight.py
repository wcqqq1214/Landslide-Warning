"""Verify feature-only intervention and exact compatibility with old endpoints."""

import os
import traceback
import numpy as np
import torch
from tide_direct.numpy_model import forward
from . import core as c


def main():
    cfg = c.spec()
    o = c.o
    o.setup(cfg)
    source_count = c.guard(False)
    root = c.ROOT / cfg["out"]
    out = root / "preflight"
    out.mkdir(exist_ok=False)
    checks = []

    def close(name, a, b, tol=1e-9):
        a, b = np.asarray(a), np.asarray(b)
        assert a.shape == b.shape, name
        d = float(np.max(abs(a - b), initial=0))
        assert np.isfinite(a).all() and np.isfinite(b).all() and d <= tol, (name, d)
        checks.append(dict(name=name, values=a.size, max_difference=d, tolerance=tol))

    original = c.old.spec()
    for key in [
        "network",
        "training",
        "optimizer",
        "normalization",
        "calibration",
        "effect",
        "points",
        "origins",
        "ends",
        "seeds",
        "updates",
        "parameters",
        "future_information",
    ]:
        assert cfg[key] == original[key], key
    y = o.labels(cfg, 612, "preflight_prefix_only")
    f, dates = o.read_forcing(c.ROOT / cfg["data"], 1461)
    ts = o.bank(cfg)
    sc = c.scaling(cfg, y, f[:612], ts[612])
    assert sc == o.read_json(c.ROOT / cfg["prior_tide_out"] / "origin_612/scaling.json")
    ms = np.array([432, 500, 611])
    values = {
        a: c.arrays(cfg, ts, y, f[:612], ms, sc, a, targets=True) for a in cfg["arms"]
    }
    for arm, vs in values.items():
        for j in [0, 2, 3]:
            close(
                arm + " shared history/target/maturity " + str(j),
                vs[j],
                values["TiDE_DATA"][j],
                0,
            )
        for j in range(12):
            kept = j < 5 or j >= 10 or j in cfg["masks"][arm]
            expect = (
                values["TiDE_PHYS"][1][..., j]
                if kept
                else torch.zeros_like(vs[1][..., j])
            )
            close(arm + " feature " + str(j), vs[1][..., j], expect, 0)
        poisoned = y.copy()
        poisoned[432:] += 1e6
        a = c.arrays(cfg, ts, y, f[:612], [432], sc, arm)
        b = c.arrays(cfg, ts, poisoned, f[:612], [432], sc, arm)
        close(arm + " future displacement isolation", a[0], b[0], 0)
        pf = f.copy()
        pf[612:] += 1000
        close(
            arm + " unknown future drivers",
            c.arrays(cfg, ts, y, pf, [432], sc, arm)[1],
            a[1],
            0,
        )
    for arm in cfg["reuse_arms"]:
        old = c.old.arrays(original, ts, y, f[:612], ms, sc, arm, targets=True)
        for j in range(4):
            close(arm + " old endpoint inputs " + str(j), values[arm][j], old[j], 0)
    for arm, columns in [
        ("TiDE_KIN", list(range(13, 22))),
        ("TiDE_HYD", list(range(8))),
        ("TiDE_DATA", list(range(22))),
    ]:
        changed = {k: {name: v.copy() for name, v in t.items()} for k, t in ts.items()}
        for t in changed.values():
            t["x"][:, columns] += 1e5
        vv = c.arrays(cfg, changed, y, f[:612], ms, sc, arm, targets=True)
        for j in range(4):
            close(
                arm + " excluded physical group invariance " + str(j),
                vv[j],
                values[arm][j],
                0,
            )
    assert np.count_nonzero(values["TiDE_KIN"][1][..., 5:7]) > 0
    assert np.count_nonzero(values["TiDE_HYD"][1][..., 7:10]) > 0
    rng = np.random.default_rng(44)
    model = c.Tide(cfg, 0)
    for n in cfg["origins"]:
        for seed in cfg["seeds"]:
            schedule = c.schedule(cfg, n, seed)
            prior = o.load_npz(
                c.checkpoint_folder(cfg, n, "TiDE_DATA", seed) / "schedule.npz"
            )["origins"]
            close(f"n{n}s{seed} unchanged schedule", schedule, prior, 0)
    for seed in cfg["seeds"]:
        model = c.Tide(cfg, seed)
        old = c.old.Tide(original, seed)
        for name, value in model.state_dict().items():
            close("same init/" + name, value, old.state_dict()[name], 0)
    for arm in cfg["arms"]:
        mu, inc = c.predict(cfg, model, ts, y, f[:905], 612, 905, sc, arm)
        close(arm + " zero persistence", mu, np.broadcast_to(y[-1], mu.shape), 0)
        close(arm + " zero increment", inc, np.zeros_like(inc), 0)
    with torch.no_grad():
        for layer in (model.temporal.b, model.temporal.skip, model.linear):
            for p in layer.parameters():
                p.copy_(torch.from_numpy(rng.normal(0, 0.01, size=p.shape)))
    for arm in cfg["arms"]:
        h, x, _, _ = values[arm]
        q = model(h, x)
        close(
            arm + " numpy nonzero forward",
            q.detach(),
            forward(model.state_dict(), h.numpy(), x.numpy()),
            1e-9,
        )
        close(arm + " h0", q.detach()[:, 0], np.zeros((3, 4)), 0)
        individual = torch.cat([model(h[i : i + 1], x[i : i + 1]) for i in range(3)])
        close(arm + " batch independence", q.detach(), individual.detach(), 1e-9)
        model.zero_grad()
        objective = c.loss(model, values[arm])
        objective.backward()
        assert torch.isfinite(objective) and all(
            p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters()
        )
    replayed = 0
    for arm in cfg["reuse_arms"]:
        for seed in cfg["seeds"]:
            dest = c.checkpoint_folder(cfg, 612, arm, seed)
            for step in cfg["checkpoints"]:
                model, scale, ck = c.reload(dest / f"e{step}.pt")
                replay = c.predict(cfg, model, ts, y, f[:905], 612, 905, scale, arm)[0]
                close(
                    f"old {arm}s{seed}e{step} unchanged prediction",
                    replay,
                    np.load(dest / f"e{step}_mean.npy"),
                    0,
                )
                replayed += 1
    # Deliberately asymmetric values identify all seven contrast formulas.
    raw = dict(zip(cfg["arms"], [1.0, 3.0, 8.0, 15.0]))
    expected = {
        "K_at_H0": 2.0,
        "K_at_H1": 7.0,
        "H_at_K0": 7.0,
        "H_at_K1": 12.0,
        "K_average": 4.5,
        "H_average": 9.5,
        "interaction": 5.0,
    }
    for k, v in c.factor_values(raw, cfg).items():
        close("factor " + k, v, expected[k], 0)
    o.write_json(
        out / "receipt.json",
        dict(
            status="passed",
            source_files=source_count,
            checks=len(checks),
            values=sum(x["values"] for x in checks),
            details=checks,
            old_checkpoints_replayed=replayed,
            optimizer_updates=0,
            parameters=110392,
        ),
    )
    o.write_json(
        root / "implementation_lock.json",
        dict(
            time_utc=o.utc(),
            files={
                os.path.relpath(p, root): o.sha(p)
                for p in (c.ROOT / "code/tide_features").glob("*.py")
            },
        ),
    )
    o.event(
        root,
        "preflight_passed",
        checks=len(checks),
        values=sum(x["values"] for x in checks),
        optimizer_updates=0,
    )
    print("preflight passed", len(checks), sum(x["values"] for x in checks), "values")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        c.o.write_json(
            c.ROOT / c.spec()["out"] / "preflight_failure.json",
            dict(time_utc=c.o.utc(), error=traceback.format_exc()),
        )
        raise
