"""No-optimizer verification of exact queries, gradients and legal data."""

import json
import time
import traceback
import numpy as np
import torch
from tide_features.audit import independent_input
from . import core as c
from .numpy_model import forward


def main():
    cfg, o = c.spec(), c.o
    o.setup(cfg)
    sources = c.guard(False)
    root = c.ROOT / cfg["out"]
    out = root / "preflight"
    out.mkdir(exist_ok=False)
    checks = []

    def close(name, a, b, tol=1e-8):
        a, b = np.asarray(a), np.asarray(b)
        assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all(), (
            name
        )
        diff = float(np.max(abs(a - b), initial=0))
        assert diff <= tol, (name, diff, tol)
        checks.append(
            dict(name=name, values=int(a.size), max_difference=diff, tolerance=tol)
        )

    try:
        teachers = o.bank(cfg)
        full_y = o.read_labels(c.ROOT / cfg["data"], 1461)
        forcing, _ = o.read_forcing(c.ROOT / cfg["data"], 1461)
        positive = []
        for n, end in zip(cfg["origins"], cfg["ends"]):
            o.check_deadline(cfg)
            y = full_y[:n]
            sc = c.scaling(cfg, y, forcing[:n], teachers[o.teacher_id(n, True)])
            assert sc == o.read_json(
                c.ROOT / cfg["prior_tide_out"] / f"origin_{n}/scaling.json"
            )
            ms = np.array([432, n - 2, n - 1])
            inputs = c.arrays(
                cfg, teachers, y, forcing[:n], ms, sc, "TiDE_KIN_PREFIX", targets=True
            )
            control = c.arrays(
                cfg, teachers, y, forcing[:n], ms, sc, "TiDE_KIN", targets=True
            )
            for i, (a, b) in enumerate(zip(inputs, control)):
                close(f"n{n}/identical_data/{i}", a, b, 0)
            future = forcing.copy()
            future[n:] = 1e8
            changed = c.arrays(
                cfg, teachers, y, future, ms, sc, "TiDE_KIN_PREFIX", targets=True
            )
            for i, (a, b) in enumerate(zip(inputs, changed)):
                close(f"n{n}/future_forcing_training_isolation/{i}", a, b, 0)
            for i, m in enumerate(ms):
                hist, cov = independent_input(
                    c.prior.spec(), teachers, y, forcing[:n], m, sc, "TiDE_KIN", n
                )
                close(f"n{n}/m{m}/independent_history", inputs[0][i], hist, 1e-10)
                close(f"n{n}/m{m}/independent_covariates", inputs[1][i], cov)
                L = min(293, n - m)
                target = np.zeros((293, 4))
                target[:L] = (y[m : m + L] - y[m - 1]) / sc["unit"]
                close(f"n{n}/m{m}/target", inputs[2][i], target, 0)
                close(f"n{n}/m{m}/mask", inputs[3][i], np.arange(293) < L, 0)
            current = c.arrays(
                cfg,
                teachers,
                y,
                forcing[:end],
                [n],
                sc,
                "TiDE_KIN_PREFIX",
                current=True,
            )
            for seed in cfg["seeds"]:
                original = c.prior.Tide(cfg, seed)
                model = c.Tide(cfg, seed)
                assert sum(p.numel() for p in model.parameters()) == 110392
                assert original.state_dict().keys() == model.state_dict().keys()
                for key, v in original.state_dict().items():
                    close(f"n{n}/s{seed}/init/{key}", v, model.state_dict()[key], 0)
                stored = torch.load(
                    c.checkpoint_folder(cfg, n, "TiDE_KIN", seed) / "e0.pt",
                    weights_only=True,
                )
                for key, v in stored["state_dict"].items():
                    close(
                        f"n{n}/s{seed}/original_init/{key}",
                        v,
                        model.state_dict()[key],
                        0,
                    )
                close(
                    f"n{n}/s{seed}/schedule",
                    c.schedule(cfg, n, seed),
                    o.load_npz(
                        c.checkpoint_folder(cfg, n, "TiDE_KIN", seed) / "schedule.npz"
                    )["origins"],
                    0,
                )
                with torch.no_grad():
                    close(
                        f"n{n}/s{seed}/zero_init",
                        model(*current[:2]),
                        np.zeros((1, 294, 4)),
                        0,
                    )
                ck = torch.load(
                    c.checkpoint_folder(cfg, n, "TiDE_KIN", seed) / "e400.pt",
                    weights_only=True,
                )
                model.load_state_dict(ck["state_dict"])
                original.load_state_dict(ck["state_dict"])
                with torch.no_grad():
                    q = model(*current[:2])
                    old = original(*current[:2])
                    npq = forward(
                        ck["state_dict"], current[0].numpy(), current[1].numpy()
                    )
                    close(f"n{n}/s{seed}/numpy", q, npq)
                    close(f"n{n}/s{seed}/origin", q[:, 0], np.zeros((1, 4)), 0)
                    close(f"n{n}/s{seed}/full_last_query", q[:, 293], old[:, 293])
                    literal = c.literal_queries(
                        model, *current[:2], cfg["invariance_cutoffs"]
                    )
                    close(
                        f"n{n}/s{seed}/literal_queries",
                        q[:, cfg["invariance_cutoffs"]],
                        literal,
                    )
                    for h in cfg["invariance_cutoffs"]:
                        x = current[1].clone()
                        x[..., 180 + h + 1 :, :10] += 37.0
                        x[..., 180 + h + 1 :, 11] = 1 - x[..., 180 + h + 1 :, 11]
                        qc = model(current[0], x)
                        close(
                            f"n{n}/s{seed}/late_input_isolation/h{h}",
                            qc[:, : h + 1],
                            q[:, : h + 1],
                            1e-9,
                        )
                    x = current[1].clone()
                    x[..., 181:211, 5] += 1.0
                    x[..., 181:211, 0] += 0.5
                    response = float((model(current[0], x) - q).abs().max())
                    assert response > 1e-9, (n, seed, "allowed input positive control")
                    old_response = float(
                        (
                            original(current[0], c.mask_after(current[1], 180, 30))[
                                :, :31
                            ]
                            - old[:, :31]
                        )
                        .abs()
                        .max()
                    )
                    assert old_response > 1e-9
                    positive.append(
                        dict(
                            origin=n,
                            seed=seed,
                            allowed_response=response,
                            unrestricted_early_response=old_response,
                        )
                    )
                if n == 792 and seed == 0:
                    # Full 294-output oracle and full parameter gradient; no optimizer.
                    model.zero_grad(set_to_none=True)
                    fast = model(*current[:2])
                    fast.square().mean().backward()
                    gradients = {
                        k: p.grad.detach().clone() for k, p in model.named_parameters()
                    }
                    model.zero_grad(set_to_none=True)
                    slow = c.literal_queries(model, *current[:2])
                    slow.square().mean().backward()
                    close("all294_literal_output", fast.detach(), slow.detach())
                    for key, p in model.named_parameters():
                        close(
                            "all294_literal_gradient/" + key,
                            gradients[key],
                            p.grad,
                            1e-8,
                        )
                    x = current[1].clone().requires_grad_(True)
                    model.zero_grad(set_to_none=True)
                    model(current[0], x)[:, 30].sum().backward()
                    close(
                        "late_input_jacobian",
                        x.grad[..., 211:, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11]],
                        0 * x.grad[..., 211:, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11]],
                        0,
                    )
                    torch.save(
                        dict(
                            state_dict=model.state_dict(),
                            scaling=sc,
                            arm="TiDE_KIN_PREFIX",
                            seed=seed,
                        ),
                        out / "reload_probe.pt",
                    )
                    restored, _, _ = c.reload(out / "reload_probe.pt")
                    with torch.no_grad():
                        close("reload", model(*current[:2]), restored(*current[:2]), 0)
                    batch = c.arrays(
                        cfg,
                        teachers,
                        y,
                        forcing[:n],
                        c.schedule(cfg, n, seed)[0],
                        sc,
                        "TiDE_KIN_PREFIX",
                        targets=True,
                    )
                    start = time.monotonic()
                    for _ in range(5):
                        model.zero_grad(set_to_none=True)
                        c.loss(model, batch).backward()
                    seconds = (time.monotonic() - start) / 5
                    o.write_json(
                        out / "runtime_probe.json",
                        dict(
                            forward_backward_seconds=seconds,
                            approx_4800_updates_seconds=4800 * seconds,
                            optimizer_updates=0,
                            note="Nonzero old weights, five gradient evaluations only; estimate excludes optimizer, IO, audits and figures",
                        ),
                    )
                del model, original
        o.write_json(out / "positive_controls.json", positive)
        receipt = dict(
            status="passed",
            source_files=sources,
            checks=len(checks),
            values=sum(v["values"] for v in checks),
            max_difference=max(v["max_difference"] for v in checks),
            parameters=110392,
            all294_output_and_gradient_equivalent=True,
            all12_nonzero_weight_probes=True,
            late_input_isolation=True,
            optimizer_updates=0,
            new_fits=0,
            new_bplus_fits=0,
            new_physical_forwards=0,
        )
        o.write_json(out / "receipt.json", receipt)
        print(json.dumps(receipt), flush=True)
    except Exception:
        o.write_json(
            out / "failure.json", dict(status="failed", error=traceback.format_exc())
        )
        raise
    finally:
        o.write_json(out / "checks.json", checks)


if __name__ == "__main__":
    main()
