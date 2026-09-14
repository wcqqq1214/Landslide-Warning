"""Bounded pretraining contract checks; no optimizer or physical forward."""

import json
import time

import numpy as np
import torch

from .core import (
    CONFIG,
    SOURCES,
    ROOT,
    GraphGRU,
    bank,
    draw_schedule,
    ema30,
    guard,
    history_raw,
    inputs,
    labels,
    own_physics,
    predict,
    reload,
    scaling,
    setup,
    sha,
    spec,
    target_bank,
    targets,
    teacher_id,
    utc,
    write_json,
)


def main():
    cfg = spec()
    setup(cfg)
    root = ROOT / cfg["out"]
    out = root / "preflight"
    out.mkdir(parents=True, exist_ok=False)
    counts = guard()
    teachers = bank(cfg)
    y = labels(cfg, 612, "preflight_prefix_only")
    sc = scaling(teachers[612], y, cfg)
    cache = target_bank(teachers, y)
    checks = {}
    parameters = {}
    for n in cfg["origins"]:
        for seed in cfg["seeds"]:
            ms, hs = draw_schedule(n, seed, cfg)
            assert ms.shape == (200, 4) and hs.shape == (200, 4, 16)
            assert np.all(ms[:, :, None] + hs - 1 < n)
            for m, h in zip(ms.ravel(), hs.reshape(-1, 16)):
                tid = teacher_id(m)
                assert tid <= m and m + max(h) <= len(teachers[tid]["mean"])
    checks["all12_schedules_teacher_target_bounds"] = "passed"
    raw = history_raw(teachers[612], y)
    altered = y.copy()
    altered[:, 3] += np.linspace(0, 1, len(y))
    changed = history_raw(teachers[612], altered)
    assert np.array_equal(raw[:, :3], changed[:, :3])
    checks["node_local_preprocessing"] = "passed"
    for j in range(4):
        np.testing.assert_array_equal(
            own_physics(teachers[612])[:, j, 0], teachers[612]["x"][:, j]
        )
    ix = np.array([[1, 5, 30], [2, 7, 60]])
    ms = np.array([500, 510])
    before = inputs(teachers, y, ms, ix, sc)
    perturbed = y.copy()
    perturbed[510:] += 1e6
    after = inputs(teachers, perturbed, ms, ix, sc)
    for a, b in zip(before, after):
        assert torch.equal(a, b)
    for m, h in zip(ms, ix):
        assert np.isfinite(targets(cache, [m], h[None], sc, True).numpy()).all()
    checks["future_labels_cannot_change_inputs"] = "passed"
    r = np.column_stack([np.arange(80, dtype=float) ** 2] * 4)
    filtered = ema30(r)
    other = r.copy()
    other[40:] += 1e6
    assert np.array_equal(filtered[:40], ema30(other)[:40])
    checks["ema_future_isolation"] = "passed"
    baseline = teachers[612]["mean"][612:905]
    probe = inputs(teachers, y, [612], np.array([[1, 2, 3, 90, 293]]), sc, True)
    reference = None
    speeds = {}
    for arm in cfg["arms"] + [cfg["conditional_arm"]]:
        model = GraphGRU(cfg, 0, arm)
        parameters[arm] = sum(p.numel() for p in model.parameters())
        if reference is None:
            reference = {k: v.clone() for k, v in model.named_parameters()}
        else:
            for k, v in model.named_parameters():
                if k.startswith("head.") and model.dual:
                    continue
                assert torch.equal(reference[k], v), k
        mean, _ = predict(model, teachers, y, 612, 905, sc)
        np.testing.assert_array_equal(mean, baseline)
        with torch.no_grad():
            model.head.weight.fill_(0.03)
            model.head.bias.fill_(0.01)
        model.eval()
        with torch.no_grad():
            result = model(*probe)
            changed = list(probe)
            changed[0] = changed[0].clone()
            changed[0][:, 3, -10:, 10:] += 0.5
            altered = model(*changed)
            delta = (result - altered).abs().max(dim=1).values[0].max(-1).values.numpy()
            assert delta[3] > 1e-12
            assert np.array_equal(delta[:2], np.zeros(2))
            assert delta[2] == 0 if arm == "GRU_LOCAL" else delta[2] > 1e-12
            checks[arm + "_neighbor_effect_mm_normalized"] = delta.tolist()
            for q in range(5):
                single = (
                    probe[0],
                    probe[1],
                    probe[2][:, q : q + 1],
                    probe[3][:, q : q + 1],
                )
                np.testing.assert_allclose(
                    model(*single).numpy(),
                    result[:, q : q + 1].numpy(),
                    atol=1e-12,
                    rtol=0,
                )
            changed = list(probe)
            changed[2] = changed[2].clone()
            changed[2][:, 3:] += 20
            np.testing.assert_array_equal(
                model(*changed)[:, :3].numpy(), result[:, :3].numpy()
            )
        save = out / f"{arm}_probe.pt"
        torch.save(
            dict(
                state_dict=model.state_dict(),
                scaling=sc,
                seed=0,
                arm=arm,
                step=0,
                training_prefix=612,
                config_sha256=sha(CONFIG),
            ),
            save,
        )
        restored, scale, _ = reload(save, cfg)
        m1, _ = predict(model, teachers, y, 612, 905, sc)
        m2, _ = predict(restored, teachers, y, 612, 905, scale)
        np.testing.assert_array_equal(m1, m2)
        start = time.monotonic()
        model.train()
        model.zero_grad(set_to_none=True)
        ms, hs = draw_schedule(612, 0, cfg)
        v = model(*inputs(teachers, y, ms[0], hs[0], sc))
        target = targets(cache, ms[0], hs[0], sc, model.dual)
        loss = (v.sum(-1) - target.sum(-1)).square().mean() + v.square().mean()
        loss.backward()
        assert all(
            torch.isfinite(p.grad).all()
            for p in model.parameters()
            if p.grad is not None
        )
        speeds[arm] = time.monotonic() - start
    checks["zero_fallback_common_initialization_reload_queries_gradient"] = (
        "passed for all3"
    )
    receipt = dict(
        status="passed",
        time_utc=utc(),
        source_files=counts,
        new_fits=0,
        optimizer_updates=0,
        physical_forwards=0,
        label_prefix_max=612,
        parameters=parameters,
        checks=checks,
        one_batch_forward_backward_seconds=speeds,
    )
    write_json(out / "receipt.json", receipt)
    files = (
        list((ROOT / "code/overnight_graph").glob("*.py"))
        + [
            CONFIG,
            SOURCES,
            ROOT / "docs/ootang_overnight_graph_plan.v1.0.md",
            ROOT / "docs/ootang_overnight_graph_implementation.v1.0.md",
        ]
        + list(out.glob("*"))
    )
    write_json(
        root / "implementation_lock.json",
        dict(time_utc=utc(), files={str(p.relative_to(ROOT)): sha(p) for p in files}),
    )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
