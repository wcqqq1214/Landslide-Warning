"""Risk-focused checks before any new optimizer update."""

import copy
import json
import traceback
import numpy as np
import torch
from . import core as c
from .numpy_model import parts
from tide_direct.numpy_model import forward as base_numpy
from tide_direct.audit import independent_input


def main():
    cfg, o = c.spec(), c.o
    o.setup(cfg)
    root = c.ROOT / cfg["out"]
    out = root / "preflight"
    out.mkdir(exist_ok=False)
    checks = []
    sources = c.guard(False)

    def close(name, a, b, tol=1e-9):
        a = a.detach().numpy() if isinstance(a, torch.Tensor) else np.asarray(a)
        b = b.detach().numpy() if isinstance(b, torch.Tensor) else np.asarray(b)
        assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all(), (
            name
        )
        d = float(np.max(np.abs(a - b), initial=0))
        assert d <= tol, (name, d)
        checks.append(dict(name=name, values=int(a.size), max_difference=d))

    def reject(name, call):
        try:
            call()
        except ValueError:
            checks.append(dict(name=name, values=0, max_difference=0))
        else:
            raise AssertionError(name)

    y = o.labels(cfg, 612, "preflight_only612_no_new_training")
    forcing, _ = o.read_forcing(c.ROOT / cfg["data"], 905)
    teachers = o.bank(cfg)
    sc = c.scaling(cfg, y, forcing[:612], teachers[612])
    close(
        "cap from prefix only",
        sc["hydro_cap_mm"],
        np.maximum(np.sqrt(np.mean((y[30:] - y[:-30]) ** 2, 0)), 1),
        0,
    )
    for key in [
        "optimizer",
        "seeds",
        "effect",
        "calibration",
        "origins",
        "ends",
        "updates",
        "checkpoints",
    ]:
        assert cfg[key] == c.fusion.spec()[key], key
    for n, count, regimes in [(612, 0, 0), (792, 20, 1), (972, 200, 5), (1168, 396, 9)]:
        ms = c.eligible(cfg, n)
        assert len(ms) == count
        assert len(set(c.base_prefix(cfg, int(m)) for m in ms)) == regimes
        if len(ms):
            assert np.all(ms + 293 <= n) and np.all(
                np.array([c.base_prefix(cfg, int(m)) for m in ms]) <= ms
            )
            for seed in cfg["seeds"]:
                close(
                    f"n{n}s{seed} common schedule",
                    c.schedule(cfg, len(ms), seed),
                    np.random.default_rng(seed).integers(0, count, size=(400, 8)),
                    0,
                )
        else:
            reject(
                "bootstrap refuses optimizer schedule", lambda: c.schedule(cfg, 0, 0)
            )
        checks.append(
            dict(name=f"whole path maturity n{n}", values=len(ms), max_difference=0)
        )
    reject(
        "partial293day residual rejected",
        lambda: c.head_targets(cfg, y, np.array([480]), np.zeros((1, 293, 4)), sc),
    )
    sy = np.arange(900 * 4, dtype=float).reshape(900, 4)
    ms = np.array([480, 500])
    forecasts = np.zeros((2, 293, 4))
    expected = np.stack([sy[m : m + 293] for m in ms]) / sc["unit"]
    close(
        "synthetic exact full target indices",
        c.head_targets(cfg, sy, ms, forecasts, sc),
        expected,
        0,
    )
    poisoned = sy.copy()
    poisoned[793:] += 1e12
    close(
        "outside target path isolation",
        c.head_targets(cfg, poisoned, ms, forecasts, sc),
        expected,
        0,
    )
    reject(
        "same model trained beyond issue rejected",
        lambda: c.base_predict(
            None, dict(fit_prefix=481), teachers, y[:480], forcing[:773], 480
        ),
    )
    raw = c.raw_cov(teachers, [480, 612])
    independent = np.zeros_like(raw)
    for j, m in enumerate([480, 612]):
        ix = np.r_[np.arange(m - 180, m), np.arange(m - 1, m + 293)]
        t = teachers[o.teacher_id(m, True)]
        for p in range(4):
            independent[j, p, :, 0] = t["moisture"][ix, p]
            independent[j, p, :, 1] = t["rain_head"][ix, p]
            independent[j, p, :, 2] = t["reservoir_head"][ix]
            independent[j, p, :, 3] = (ix - m + 1) / 293
            independent[j, p, :, 4] = 1
    close("H fields dates and channels", raw, independent, 0)
    changed = raw.copy()
    changed[..., :3] += 1000
    close(
        "CAL cannot read H",
        c.head_cov(raw, sc, "TiDE_CAL"),
        c.head_cov(changed, sc, "TiDE_CAL"),
        0,
    )
    assert not np.array_equal(
        c.head_cov(raw, sc, "TiDE_HCAL"), c.head_cov(changed, sc, "TiDE_HCAL")
    )
    for r in [480, 540, 612]:
        rs = c.old.scaling(
            c.old.spec(), y[:r], forcing[:r], teachers[o.teacher_id(r, True)]
        )
        sample = [432, r - 1]
        a = c.base_arrays(teachers, y[:r], forcing[:r], sample, rs, targets=True)
        for j, m in enumerate(sample):
            h, x = independent_input(
                c.old.spec(), teachers, y[:r], forcing[:r], m, rs, "TiDE_PHYS", r
            )
            x[..., 7:10] = 0
            close(f"r{r}m{m} old history", a[0][j], h, 0)
            close(f"r{r}m{m} old covariates", a[1][j], x, 1e-8)
            mature = min(293, r - m)
            close(
                f"r{r}m{m} target",
                a[2][j, :mature],
                (y[m : m + mature] - y[m - 1]) / rs["unit"],
                0,
            )
            assert torch.count_nonzero(a[1][j, :, 180 + mature + 1 :, :10]) == 0
        fy = y[:r].copy()
        fy[432:] += 1e8
        close(
            "historical input displacement isolation",
            c.base_arrays(teachers, fy, forcing[:r], [432], rs)[0],
            a[0][:1],
            0,
        )
        ff = forcing.copy()
        ff[r:] += 1e8
        close(
            "historical input future forcing mask",
            c.base_arrays(teachers, y[:r], ff, sample, rs, targets=True)[1],
            a[1],
            0,
        )
    for seed in cfg["seeds"]:
        basefile = c.base_path(cfg, 612, seed)
        before = o.sha(basefile)
        base, bs, _ = c.base_reload(basefile)
        mu = c.base_predict(base, bs, teachers, y, forcing, 612)
        close(
            f"original KIN seed{seed} unchanged",
            mu,
            np.load(basefile.with_name("e400_mean.npy")),
            0,
        )
        av = c.base_arrays(teachers, y, forcing, [612], bs, current=True)
        close(
            "original KIN independent forward",
            mu,
            y[-1]
            + base_numpy(base.state_dict(), av[0].numpy(), av[1].numpy())[0, 1:]
            * np.asarray(bs["unit"]),
            1e-8,
        )
        a = c.Head(cfg, seed, sc)
        b = c.Head(cfg, seed, sc)
        assert sum(p.numel() for p in a.parameters()) == 13179
        assert not any(p.requires_grad for p in base.parameters())
        assert not set(map(id, a.parameters())) & set(map(id, base.parameters()))
        for name, v in a.state_dict().items():
            close("paired init " + name, v, b.state_dict()[name], 0)
        for arm in cfg["new_arms"]:
            x = torch.from_numpy(c.head_cov(raw, sc, arm))
            q = a(x)
            close(
                "zero head exact KIN fallback",
                mu + q.detach().numpy()[0, 1:] * sc["unit"],
                mu,
                0,
            )
            (q[:, 1:] - 1).square().mean().backward()
            assert all(p.grad is None for p in base.parameters())
        assert o.sha(basefile) == before
        rng = np.random.default_rng(seed + 789)
        with torch.no_grad():
            for layer in [a.h_decoder.b, a.h_decoder.skip]:
                for p in layer.parameters():
                    p.copy_(torch.tensor(rng.normal(0, 0.01, size=p.shape)))
        for arm in cfg["new_arms"]:
            x = torch.from_numpy(c.head_cov(raw, sc, arm))
            qa, qb = a.parts(x)
            na, nb = parts(a.state_dict(), x.numpy())
            close("nonzero independent raw", qa, na, 1e-9)
            close("nonzero independent actual", qb, nb, 1e-9)
            close("h0 anchored", qb[:, 0], np.zeros((2, 4)), 0)
            close(
                "batch independence",
                qb,
                torch.cat([a(x[i : i + 1]) for i in range(2)]),
                1e-9,
            )
            assert np.all(
                np.abs(qb.detach().numpy())
                <= np.asarray(sc["hydro_cap_mm"]) / sc["unit"] + 1e-12
            )
        xa = torch.from_numpy(c.head_cov(raw, sc, "TiDE_HCAL"))
        xb = torch.from_numpy(c.head_cov(changed, sc, "TiDE_HCAL"))
        assert float((a(xa) - a(xb)).abs().max().detach()) > 1e-8
        p = out / f"nonzero_seed{seed}.pt"
        torch.save(dict(state_dict=a.state_dict(), seed=seed, scaling=sc), p)
        re, _, _ = c.reload(p)
        close("reload nonzero", a(xa), re(xa), 0)
        for sign in [-1, 1]:
            extreme = copy.deepcopy(a)
            with torch.no_grad():
                for p in extreme.parameters():
                    p.zero_()
                extreme.h_decoder.skip.bias.copy_(
                    sign * torch.arange(294, dtype=torch.float64) * 1e12
                )
            q = extreme(xa).detach().numpy()
            cap = np.asarray(sc["hydro_cap_mm"]) / sc["unit"]
            close(
                "arbitrary large raw cap",
                q[:, 1:],
                np.broadcast_to(sign * cap, q[:, 1:].shape),
                1e-12,
            )
    receipt = dict(
        status="passed",
        source_files=sources,
        checks=len(checks),
        values=sum(v["values"] for v in checks),
        max_difference=max(v["max_difference"] for v in checks),
        optimizer_updates=0,
        base_refits=0,
        frozen_base_in_optimizer=False,
        bootstrap612="zero without fit",
        eligible_counts=[0, 20, 200, 396],
        base_regime_counts=[0, 1, 5, 9],
    )
    o.write_json(out / "checks.json", checks)
    o.write_json(out / "receipt.json", receipt)
    o.event(root, "preflight_passed", **receipt)
    print(json.dumps(receipt))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        c.o.write_json(
            c.ROOT / c.spec()["out"] / "preflight_error.json",
            dict(error=traceback.format_exc(), optimizer_updates=0),
        )
        raise
