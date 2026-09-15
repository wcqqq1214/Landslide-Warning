"""Boundary, pairing, dense-forward and serialization verification before fitting."""

import copy
import os
import json
import platform
import subprocess
import sys
import traceback
import importlib.metadata as metadata
import numpy as np
import torch
from . import core as c
from .numpy_model import forward


def main():
    cfg = c.spec()
    o = c.o
    o.setup(cfg)
    count = c.guard(False)
    root = c.ROOT / cfg["out"]
    out = root / "preflight"
    out.mkdir(exist_ok=False)
    checks = []

    def close(name, a, b, tol=1e-10):
        a, b = np.asarray(a), np.asarray(b)
        assert a.shape == b.shape, (name, a.shape, b.shape)
        d = float(np.max(abs(a - b), initial=0))
        assert np.isfinite(a).all() and np.isfinite(b).all() and d <= tol, (name, d)
        checks.append(dict(name=name, values=a.size, max_abs=d, tolerance=tol))

    y = o.labels(cfg, 612, "preflight_prefix_only")
    forcing, dates = o.read_forcing(c.ROOT / cfg["data"], 1461)
    teachers = o.bank(cfg)
    sc = c.scaling(cfg, y, forcing[:612], teachers[612])
    close("teacher-independent units", sc["unit"], np.maximum(y.std(0), 1.0), 0)
    d = c.driver_features(forcing)
    close(
        "rain sum7 independent",
        d[:, 3],
        np.array([forcing[max(0, i - 6) : i + 1, 0].sum() for i in range(1461)]),
        1e-8,
    )
    close(
        "rain sum30 independent",
        d[:, 4],
        np.array([forcing[max(0, i - 29) : i + 1, 0].sum() for i in range(1461)]),
        1e-8,
    )
    ms = np.array([432, 500, 611])
    v = {}
    for arm in cfg["arms"]:
        v[arm] = c.arrays(cfg, teachers, y, forcing[:612], ms, sc, arm, targets=True)
        hist, cov, target, mask = [a.numpy() for a in v[arm]]
        for i, m in enumerate(ms):
            close(
                f"{arm} history m{m}",
                hist[i],
                ((y[m - 180 : m] - y[m - 1]) / sc["unit"]).T,
                0,
            )
            mature = min(293, 612 - m)
            close(
                f"{arm} target m{m}",
                target[i, :mature],
                (y[m : m + mature] - y[m - 1]) / sc["unit"],
                0,
            )
            assert (
                mask[i].sum() == mature and cov[i, 0, :, 11].sum() == 180 + 1 + mature
            )
            close(
                f"{arm} unknown feature zero m{m}",
                cov[i, :, 181 + mature :, :10],
                np.zeros_like(cov[i, :, 181 + mature :, :10]),
                0,
            )
        changed_y = y.copy()
        changed_y[432:] += 99999
        original = c.arrays(cfg, teachers, y, forcing, [432], sc, arm)
        changed = c.arrays(cfg, teachers, changed_y, forcing, [432], sc, arm)
        for k in range(2):
            close(
                f"{arm} future displacement isolation {k}", original[k], changed[k], 0
            )
        ff = forcing.copy()
        ff[612:] = 777
        changed = c.arrays(cfg, teachers, y, ff, [432], sc, arm)
        for k in range(2):
            close(
                f"{arm} unknown future drivers isolation {k}",
                original[k],
                changed[k],
                0,
            )
    close("paired identical histories", v["TiDE_DATA"][0], v["TiDE_PHYS"][0], 0)
    close("paired identical targets", v["TiDE_DATA"][2], v["TiDE_PHYS"][2], 0)
    close("paired identical mature masks", v["TiDE_DATA"][3], v["TiDE_PHYS"][3], 0)
    altered = copy.deepcopy(sc)
    altered["physics_mean"] = (np.asarray(sc["physics_mean"]) + 1e6).tolist()
    altered["physics_std"] = (np.asarray(sc["physics_std"]) * 999).tolist()
    independent = c.arrays(
        cfg, {}, y, forcing[:612], ms, altered, "TiDE_DATA", targets=True
    )
    for k in range(4):
        close(
            f"DATA requires no teacher and ignores physics scaler {k}",
            independent[k],
            v["TiDE_DATA"][k],
            0,
        )
    synthetic = np.cumsum(np.random.default_rng(101).normal(size=(1461, 4)), axis=0)
    support = []
    for n, end in zip(cfg["origins"], cfg["ends"]):
        legal = np.arange(432, n)
        ids = [o.teacher_id(int(m)) for m in legal]
        for m, tid in zip(legal, ids):
            assert int(teachers[tid]["teacher_fit_prefix"]) <= m
            assert min(n, m + 293) <= len(teachers[tid]["mean"])
        ns = c.scaling(cfg, synthetic[:n], forcing[:n], teachers[o.teacher_id(n, True)])
        current = c.arrays(
            cfg,
            teachers,
            synthetic[:n],
            forcing[:end],
            [n],
            ns,
            "TiDE_PHYS",
            current=True,
        )
        assert current[1][..., 11].min() == 1
        for seed in cfg["seeds"]:
            a = c.schedule(cfg, n, seed)
            b = c.schedule(cfg, n, seed)
            close(f"paired schedule n{n} s{seed}", a, b, 0)
        support.append(
            dict(
                n=n,
                origins=len(legal),
                full293=int(np.sum(n - legal >= 293)),
                last_full_origin=int(n - 293) if n - 293 >= 432 else None,
                issue_date=str(dates[n - 1]),
                first_target=str(dates[n]),
                last_target=str(dates[end - 1]),
            )
        )
    model = c.Tide(cfg, 0)
    peer = c.Tide(cfg, 0)
    for name, value in model.state_dict().items():
        close("paired init " + name, value, peer.state_dict()[name], 0)
    for arm in cfg["arms"]:
        mu, _ = c.predict(cfg, model, teachers, y, forcing[:905], 612, 905, sc, arm)
        close(arm + " zero output persistence", mu, np.broadcast_to(y[-1], (293, 4)), 0)
    # Nonzero heads exercise every path without performing optimizer updates.
    rng = np.random.default_rng(217)
    with torch.no_grad():
        for layer in (model.temporal.b, model.temporal.skip, model.linear):
            for p in layer.parameters():
                p.copy_(torch.from_numpy(rng.normal(0, 0.01, size=p.shape)))
    for arm in cfg["arms"]:
        h, x, _, _ = v[arm]
        q = model(h, x)
        close(arm + " anchored h0", q.detach()[:, 0], np.zeros((len(ms), 4)), 0)
        close(
            arm + " independent numpy",
            q.detach(),
            forward(model.state_dict(), h.numpy(), x.numpy()),
            1e-9,
        )
        individual = torch.cat(
            [model(h[i : i + 1], x[i : i + 1]) for i in range(len(ms))]
        )
        close(arm + " batch independence", q.detach(), individual.detach(), 1e-9)
        model.zero_grad()
        loss = c.loss(model, v[arm])
        loss.backward()
        assert torch.isfinite(loss) and all(
            p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters()
        )
        idx = (0, 0)
        p = model.encoder.a.weight
        grad = float(p.grad[idx])
        eps = 1e-5
        with torch.no_grad():
            original = float(p[idx])
            p[idx] = original + eps
            plus = float(c.loss(model, v[arm]))
            p[idx] = original - eps
            minus = float(c.loss(model, v[arm]))
            p[idx] = original
        close(
            arm + " finite difference encoder gradient",
            grad,
            (plus - minus) / (2 * eps),
            1e-7,
        )
    checkpoint = out / "nontrained_forward_probe.pt"
    torch.save(
        dict(state_dict=model.state_dict(), scaling=sc, seed=0, arm="probe", step=0),
        checkpoint,
    )
    reloaded, rs, _ = c.reload(checkpoint)
    for arm in cfg["arms"]:
        a = c.predict(cfg, model, teachers, y, forcing[:905], 612, 905, sc, arm)[0]
        b = c.predict(cfg, reloaded, teachers, y, forcing[:905], 612, 905, rs, arm)[0]
        close(arm + " reload", a, b, 1e-9)
    # Conditional whole-future input may alter early forecasts; this is allowed, not leakage.
    full = c.arrays(
        cfg, teachers, y, forcing[:905], [612], sc, "TiDE_DATA", current=True
    )
    changed_forcing = forcing[:905].copy()
    changed_forcing[850:, 0] += 20
    changed = c.arrays(
        cfg, teachers, y, changed_forcing, [612], sc, "TiDE_DATA", current=True
    )
    with torch.no_grad():
        delta = float(abs(model(*full[:2])[0, 1] - model(*changed[:2])[0, 1]).max())
    assert delta > 0
    o.write_json(
        out / "receipt.json",
        dict(
            status="passed",
            source_files=count,
            parameters=cfg["parameters"],
            checks=checks,
            values=sum(x["values"] for x in checks),
            support=support,
            optimizer_updates=0,
            later_given_driver_changes_early_output=delta,
            scope="preflight uses actual labels through 612 only; later-prefix checks use synthetic labels",
        ),
    )
    o.write_json(
        root / "environment.json",
        dict(
            python=sys.version,
            executable=sys.executable,
            platform=platform.platform(),
            packages={
                k: metadata.version(k)
                for k in ["numpy", "scipy", "torch", "pandas", "matplotlib"]
            },
            dependencies=json.loads(
                subprocess.check_output(
                    [
                        "uv",
                        "pip",
                        "list",
                        "--python",
                        sys.executable,
                        "--format",
                        "json",
                    ]
                )
            ),
            plan_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            original_environment_unchanged=True,
        ),
    )
    paths = list((c.ROOT / "code/tide_direct").glob("*.py"))
    o.write_json(
        root / "implementation_lock.json",
        dict(
            time_utc=o.utc(), files={os.path.relpath(p, root): o.sha(p) for p in paths}
        ),
    )
    o.event(
        root,
        "preflight_passed",
        checks=len(checks),
        values=sum(x["values"] for x in checks),
        optimizer_updates=0,
    )
    print(
        "preflight passed",
        len(checks),
        "checks",
        sum(x["values"] for x in checks),
        "values",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        c.o.write_json(
            c.ROOT / c.spec()["out"] / "preflight_failure.json",
            dict(time_utc=c.o.utc(), error=traceback.format_exc()),
        )
        raise
