"""One optimizer update per full epoch; common checkpoint selection across seeds."""

import time
from pathlib import Path
import numpy as np
import torch
from .data import COUNTS
from .features import point_features
from .mechanics import tensor
from .models import M1, M2, ScaleHead, scale_features
from .probability import crps
from .reference import save_json


def setup(seed):
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1) if torch.get_num_interop_threads() != 1 else None
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(seed)
    np.random.seed(seed)


def optimizer(model):
    return torch.optim.Adam(
        model.parameters(), lr=1e-3, betas=(0.9, 0.999), eps=1e-8, weight_decay=0
    )


def update(model, op):
    grads = [p.grad for p in model.parameters() if p.grad is not None]
    if not grads or not all(torch.isfinite(g).all() for g in grads):
        raise ArithmeticError("Nonfinite or missing gradient")
    norm = torch.nn.utils.clip_grad_norm_(
        model.parameters(), 1.0, error_if_nonfinite=True
    )
    op.step()
    if not all(torch.isfinite(p).all() for p in model.parameters()):
        raise ArithmeticError("Nonfinite parameters")
    return float(norm)


def mean_epoch(model, route, op, drivers, mechanics, scaler, labels, chunk=128):
    op.zero_grad(set_to_none=True)
    if route == "M1":
        x = tensor(scaler.transform(point_features(drivers, mechanics)))
        base = tensor(mechanics.u + drivers.y0)
        loss_value = 0.0
        # Each chunk contributes to exactly the same point/day-averaged loss.
        for start in range(30, len(labels), chunk):
            ids = range(start, min(start + chunk, len(labels)))
            windows = torch.stack([x[t - 29 : t + 1, :, None, :] for t in ids])
            pred = base[start : start + len(windows)] + model(windows)
            loss = (
                (pred - tensor(labels[start : start + len(windows)])) / 100
            ).square().sum() / ((len(labels) - 30) * 4)
            loss.backward()
            loss_value += float(loss.detach())
    else:
        pred = model.predict(drivers, mechanics, scaler)
        loss = ((pred[30:] - tensor(labels[30:])) / 100).square().mean()
        loss.backward()
        loss_value = float(loss.detach())
    norm = update(model, op)
    return loss_value, norm


def choose(scores):
    best = min(scores)
    for epoch in sorted(scores):
        if scores[epoch] < scores[best] - 1e-12:
            best = epoch
    return best


def mean_model(route, seed):
    setup(seed)
    return M1() if route == "M1" else M2()


def predict_mean(model, route, drivers, mechanics, scalers):
    with torch.no_grad():
        mu = model.predict(
            drivers, mechanics, scalers[0 if route == "M1" else 1]
        ).numpy()
    if not np.isfinite(mu[30:]).all():
        raise ArithmeticError("Nonfinite mean prediction")
    return mu


def train_route(
    route,
    stage,
    train_drivers,
    train_mechanics,
    full_drivers,
    full_mechanics,
    scalers,
    train_labels,
    development_labels,
    output,
    locked=None,
):
    """Final labels never enter this API; development labels only score fixed checkpoints."""
    nfit, nfull = COUNTS[stage]
    if len(train_drivers.dates) != nfit or train_labels.shape != (nfit, 4):
        raise ValueError("Trainer label/forcing boundary violation")
    if len(full_drivers.dates) != nfull:
        raise ValueError("Invalid prediction horizon")
    if stage == "development" and development_labels.shape != (376, 4):
        raise ValueError("Development labels must contain only the prescribed 376 days")
    if stage == "final" and (development_labels is not None or locked is None):
        raise ValueError(
            "Final run requires locked epochs and accepts no held-out labels"
        )
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    mean_max = 200 if stage == "development" else locked["e_mu"]
    scale_max = 100 if stage == "development" else locked["e_sigma"]
    mean_candidates = list(range(0, 201, 10)) if stage == "development" else [mean_max]
    scale_candidates = (
        list(range(0, 101, 10)) if stage == "development" else [scale_max]
    )
    means_by_epoch = {e: [] for e in mean_candidates}
    logs = []
    for seed in range(3):
        model = mean_model(route, seed)
        op = optimizer(model)
        tick = time.monotonic()
        for epoch in range(mean_max + 1):
            if epoch:
                loss, norm = mean_epoch(
                    model,
                    route,
                    op,
                    train_drivers,
                    train_mechanics,
                    scalers[0 if route == "M1" else 1],
                    train_labels,
                )
                logs.append(
                    dict(
                        seed=seed,
                        head="mean",
                        epoch=epoch,
                        loss=loss,
                        gradient_norm=norm,
                    )
                )
            if epoch in mean_candidates:
                torch.save(model.state_dict(), output / f"mean_seed{seed}_e{epoch}.pt")
                means_by_epoch[epoch].append(
                    predict_mean(model, route, full_drivers, full_mechanics, scalers)
                )
                print(
                    f"{stage} {route} seed={seed} mean epoch={epoch} elapsed={time.monotonic() - tick:.1f}s",
                    flush=True,
                )
        save_json(
            output / f"mean_training_seed{seed}.json",
            [x for x in logs if x["seed"] == seed],
        )
    mean_scores = {}
    if stage == "development":
        for e, values in means_by_epoch.items():
            residual = np.mean(values, axis=0)[nfit:] - development_labels
            mean_scores[e] = float(np.sqrt(np.mean(residual**2, axis=0)).mean())
        e_mu = choose(mean_scores)
    else:
        e_mu = mean_max
    means = np.stack(means_by_epoch[e_mu])
    if route == "M2":
        diagnostics = []
        for seed in range(3):
            selected_model = mean_model(route, seed)
            selected_model.load_state_dict(
                torch.load(output / f"mean_seed{seed}_e{e_mu}.pt", weights_only=True)
            )
            with torch.no_grad():
                _, states, factors, checks = selected_model.predict(
                    full_drivers, full_mechanics, scalers[1], return_states=True
                )
            np.savez_compressed(
                output / f"states_seed{seed}.npz",
                states=states.numpy(),
                multipliers=factors.numpy(),
                substep_audit=checks.numpy(),
            )
            diagnostics.append(
                dict(
                    seed=seed,
                    min_multiplier=float(factors.min()),
                    max_multiplier=float(factors.max()),
                    near_lower_fraction=float((factors <= 0.505).double().mean()),
                    near_upper_fraction=float((factors >= 1.98).double().mean()),
                    saturation_definition="within 1% of 0.5/2 bounds; diagnostic only",
                    substeps_checked=(len(full_drivers.dates) - 1) * 64,
                    max_normalized_complementarity=float(checks[:, 2].max()),
                    active_set_changes=int(checks[:, 4].sum()),
                    zero_dry_coefficients=(full_mechanics.theta[24:28] == 0).tolist(),
                    zero_wet_coefficients=(full_mechanics.theta[47:51] == 0).tolist(),
                )
            )
        save_json(output / "mechanical_diagnostics.json", diagnostics)
    sigma_by_epoch = {e: [] for e in scale_candidates}
    for seed in range(3):
        setup(seed + 10000)
        rmse = np.sqrt(np.mean((means[seed, 30:nfit] - train_labels[30:]) ** 2))
        head = ScaleHead(rmse)
        op = optimizer(head)
        x = scale_features(full_drivers, full_mechanics, scalers[0], means[seed])
        mu = tensor(means[seed, 30:nfit])
        target = tensor(train_labels[30:])
        for epoch in range(scale_max + 1):
            if epoch:
                op.zero_grad(set_to_none=True)
                sigma = head(x[30:nfit])
                loss = (
                    torch.log(sigma / 100) + 0.5 * ((target - mu) / sigma).square()
                ).mean()
                if not torch.isfinite(loss):
                    raise ArithmeticError("Nonfinite Gaussian NLL")
                loss.backward()
                norm = update(head, op)
                logs.append(
                    dict(
                        seed=seed,
                        head="scale",
                        epoch=epoch,
                        loss=float(loss.detach()),
                        gradient_norm=norm,
                    )
                )
            if epoch in scale_candidates:
                torch.save(head.state_dict(), output / f"scale_seed{seed}_e{epoch}.pt")
                with torch.no_grad():
                    valid = head(x[30:]).numpy()
                sigma = np.vstack([np.full((30, 4), np.nan), valid])
                sigma_by_epoch[epoch].append(sigma)
        save_json(
            output / f"scale_training_seed{seed}.json",
            [x for x in logs if x["seed"] == seed and x["head"] == "scale"],
        )
    scale_scores = {}
    if stage == "development":
        for e, values in sigma_by_epoch.items():
            scale_scores[e] = float(
                crps(
                    means[:, nfit:], np.stack(values)[:, nfit:], development_labels
                ).mean()
            )
        e_sigma = choose(scale_scores)
    else:
        e_sigma = scale_max
    sigmas = np.stack(sigma_by_epoch[e_sigma])
    if not np.isfinite(sigmas[:, 30:]).all() or np.any(sigmas[:, 30:] <= 0):
        raise ArithmeticError("Invalid complete mixture")
    model = mean_model(route, 0)
    parameter_count = sum(p.numel() for p in model.parameters()) + sum(
        p.numel() for p in ScaleHead(1).parameters()
    )
    selection = dict(
        route=route,
        stage=stage,
        status="complete",
        e_mu=e_mu,
        e_sigma=e_sigma,
        mean_candidate_rmse_mm=mean_scores,
        scale_candidate_crps_mm=scale_scores,
        parameter_count=parameter_count,
        seeds=[0, 1, 2],
    )
    save_json(output / "selection.json", selection)
    np.savez_compressed(output / "selected_predictions.npz", means=means, sigmas=sigmas)
    return means, sigmas, selection


def rank_routes(selections, baseline_rmse):
    eligible = {k: v for k, v in selections.items() if v.get("status") == "complete"}
    if not eligible:
        return dict(
            candidate=None,
            retained_baseline="M0",
            reason="No complete three-seed route",
        )
    scores = {
        k: float(
            v["mean_candidate_rmse_mm"][str(v["e_mu"])]
            if str(v["e_mu"]) in v["mean_candidate_rmse_mm"]
            else v["mean_candidate_rmse_mm"][v["e_mu"]]
        )
        for k, v in eligible.items()
    }
    crps_scores = {
        k: float(
            v["scale_candidate_crps_mm"].get(
                str(v["e_sigma"]), v["scale_candidate_crps_mm"].get(v["e_sigma"])
            )
        )
        for k, v in eligible.items()
    }
    best = next(iter(eligible))
    steps = []
    if len(eligible) == 2:
        a, b = "M1", "M2"
        if abs(scores[a] - scores[b]) > 0.01 * min(scores.values()):
            best = min(scores, key=scores.get)
            steps.append("RMSE gap exceeds 1% of smaller RMSE")
        elif abs(crps_scores[a] - crps_scores[b]) > 1e-12:
            best = min(crps_scores, key=crps_scores.get)
            steps.append("RMSE close; compare mixture CRPS")
        else:
            best = min(eligible, key=lambda k: (eligible[k]["parameter_count"], k))
            steps.append("CRPS tied; parameter count then M1")
    retain = "M0" if all(v >= baseline_rmse for v in scores.values()) else None
    return dict(
        candidate=best,
        rmse_mm=scores,
        crps_mm=crps_scores,
        steps=steps,
        baseline_rmse_mm=baseline_rmse,
        retained_baseline=retain,
        selection_data="development only; final historical labels excluded",
    )
