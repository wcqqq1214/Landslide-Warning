"""Check dates, missing bootstrap uncertainty, weights and fixed-state replay."""

import numpy as np
from tide_direct.audit import independent_input
from tide_direct.numpy_model import forward
from . import core as c


def main():
    cfg, o = c.spec(), c.o
    o.setup(cfg)
    sources = c.guard(False)
    root = c.ROOT / cfg["out"]
    out = root / "preflight"
    out.mkdir(exist_ok=False)
    checks = []

    def close(name, a, b, tol=1e-9):
        a, b = np.asarray(a), np.asarray(b)
        assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all(), (
            name
        )
        delta = float(np.max(abs(a - b), initial=0))
        assert delta <= tol, (name, delta)
        checks.append(dict(name=name, values=int(a.size), max_difference=delta))

    h = c.supervision(435, np.array([[432, 433, 434, 432, 433, 434, 432, 433]]), 1)
    close(
        "three-origin expected weight",
        h["expected_loss_weight"][:3],
        [11 / 18, 5 / 18, 1 / 9],
    )
    close("three-origin expected support", h["eligible_count"][:4], [3, 2, 1, 0], 0)
    close("three-origin actual support", h["sampled_terms"][:4], [8, 6, 3, 0], 0)
    close("expected mass", h["expected_loss_weight"].sum(), 1)
    close("actual mass", h["actual_loss_weight"].sum(), 1)
    teachers = o.bank(c.prior.spec())
    y = o.read_labels(c.ROOT / cfg["data"], 1461)
    f, _ = o.read_forcing(c.ROOT / cfg["data"], 1461)
    for n in cfg["origins"]:
        model, sc, ck = c.reload(cfg, n, 0, 400)
        assert not model.training and not any(
            p.requires_grad for p in model.parameters()
        )
        schedule = np.load(c.folder(cfg, n, 0) / "schedule.npz")["origins"]
        close(
            f"n{n} source schedule",
            schedule,
            np.random.default_rng(0).integers(432, n, (400, 8)),
            0,
        )
        for k in cfg["steps"]:
            v = c.supervision(n, schedule, k)
            close(f"n{n}e{k} mass", v["actual_loss_weight"].sum(), 0 if not k else 1)
        origins = np.array([432, n - 30, n - 1])
        inp = c.inputs(teachers, y[:n], f, n, sc, origins)
        for j, m in enumerate(origins):
            hi, xi = independent_input(
                c.prior.old.spec(), teachers, y[:n], f[:n], int(m), sc, "TiDE_PHYS", n
            )
            xi[..., 7:10] = 0
            close(f"n{n}m{m} history", inp[0][j], hi, 0)
            close(f"n{n}m{m} covariates", inp[1][j], xi)
        poisoned = f.copy()
        poisoned[n:] += 1e6
        close(
            f"n{n} unobserved forcing isolation",
            c.inputs(teachers, y[:n], poisoned, n, sc, origins)[1],
            inp[1],
            0,
        )
        altered = y[:n].copy()
        altered[432:] += 1e6
        changed = c.inputs(teachers, altered, f, n, sc, [432])
        close(f"n{n} future labels absent from history", changed[0][0], inp[0][0], 0)
        full = c.inputs(teachers, y[:n], f, n, sc)
        q = c.model_forward(model, full)
        close(
            f"n{n} numpy", q, forward(ck["state_dict"], *[t.numpy() for t in full[:2]])
        )
        close(f"n{n} h0", q[:, 0], np.zeros((1, 4)), 0)
        close(
            f"n{n} exact original mean",
            y[n - 1] + q[0, 1:] * np.asarray(sc["unit"]),
            np.load(c.folder(cfg, n, 0) / "e400_mean.npy"),
            0,
        )
        for d in cfg["visibility"]:
            masked = c.inputs(teachers, y[:n], f, n, sc, visible=d)
            expect = full[1].clone()
            expect[:, :, 181 + d :, :10] = 0
            expect[:, :, 181 + d :, 11] = 0
            close(f"n{n}d{d} exact mask", masked[1], expect, 0)
            close(f"n{n}d{d} fixed history", masked[0], full[0], 0)
            qi = c.model_forward(model, masked)
            close(
                f"n{n}d{d} independent forward",
                qi,
                forward(ck["state_dict"], *[t.numpy() for t in masked[:2]]),
            )
            close(f"n{n}d{d} h0", qi[:, 0], np.zeros((1, 4)), 0)
        zero, _, _ = c.reload(cfg, n, 0, 0)
        close(
            f"n{n} zero checkpoint",
            c.model_forward(zero, full),
            np.zeros((1, 294, 4)),
            0,
        )
        assert all(p.grad is None for p in model.parameters())
    assert not (c.ROOT / cfg["prior_out"] / "origin_612/sigmas.npz").exists()
    receipt = dict(
        status="passed",
        source_files=sources,
        checks=len(checks),
        values=sum(v["values"] for v in checks),
        max_difference=max(v["max_difference"] for v in checks),
        new_training=0,
        optimizer_updates=0,
        new_bplus_fits=0,
        new_physical_forwards=0,
        bootstrap_probability="unavailable",
    )
    o.write_json(out / "checks.json", checks)
    o.write_json(out / "receipt.json", receipt)
    print(receipt, flush=True)


if __name__ == "__main__":
    main()
