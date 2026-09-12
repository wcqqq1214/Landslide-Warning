"""v2.4 paired likelihood-gradient experiment; frozen B+ arrays only."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from physics_guided.features import Scaler
from physics_guided.training import setup, update
from physics_guided_balanced_origin.core import profile
from physics_guided_direct import ConditionalScale, DirectConvLSTM, evaluation_batch
from physics_guided_origin_learning.core import query_table
from physics_guided_sequence_learning.core import block_losses, make_batch
from physics_guided_sample_learning.core import POINTS
from physics_guided.probability import crps, interval_score
from physics_guided_direct import score

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/ootang_convlstm_joint.v2_4.json"
CONFIG_SHA = "fad908a228e477717160738f7dd8fac01e0ce4e6c1574f55f16daccdf692561f"
ARMS = ("DETACHED", "JOINT")
STRATEGIES = ("P0", "DIRECT_V2_0", *ARMS)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def check_hashes(mapping):
    for name, digest in mapping.items():
        if sha(ROOT / name) != digest:
            raise ValueError(f"Frozen source changed: {name}")


def specification():
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Frozen v2.4 execution configuration changed")
    spec = json.loads(CONFIG.read_text())
    check_hashes(spec["source_sha256"])
    return spec


def saved_scalers(spec, h):
    data = json.loads(
        (ROOT / spec["reference_source"] / f"scalers_{h}.json").read_text()
    )
    return tuple(
        Scaler(*(np.asarray(data[k][v]) for v in ("mean", "scale", "floor")))
        for k in ("physical", "history")
    )


@dataclass
class RowContext:
    baseline: torch.Tensor
    constants: torch.Tensor

    def features(self, mean):
        if mean.shape != self.baseline.shape:
            raise ValueError("Mean must keep query-row and point identity")
        return torch.stack(
            (
                self.constants[..., 0],
                self.constants[..., 1],
                torch.tanh((mean - self.baseline) / 100),
                self.constants[..., 3],
                self.constants[..., 4],
            ),
            dim=-1,
        )


def row_context(table, pool, labels, teacher_column):
    """Every row retains its own teacher and pre-origin observation history."""
    baselines, values = [], []
    for row in table.to_dict("records"):
        o, t, k = (int(row[x]) for x in ("origin", "target", teacher_column))
        base = pool[k][0]
        if not (30 <= o <= len(labels) and 0 <= t - o < 180 and t < len(base)):
            raise ValueError("Scale query exceeds the available causal history")
        past = labels[o - 30 : o] - base[o - 30 : o]
        if past.shape != (30, 4) or not np.isfinite(past).all():
            raise ValueError("Invalid strictly pre-origin scale history")
        baselines.append(base[t])
        values.append(
            np.stack(
                [
                    np.full(4, (t - o) / 179),
                    np.tanh((base[t] - base[o - 1]) / 100),
                    np.zeros(4),
                    np.tanh(past[-1] / 100),
                    np.tanh(np.std(past, axis=0) / 100),
                ],
                axis=-1,
            )
        )
    base = torch.tensor(np.asarray(baselines), dtype=torch.float64)
    constants = torch.tensor(np.asarray(values), dtype=torch.float64)
    if not torch.isfinite(base).all() or not torch.isfinite(constants).all():
        raise ValueError("Nonfinite scale features")
    return RowContext(base, constants)


def selected_mean(batch, correction):
    selected = (batch.row_batch, batch.row_lead)
    return batch.baseline[selected] + correction[selected]


def initial_scale(batch):
    mask = batch.blocks == 1
    if batch.targets is None or not mask.any():
        raise ValueError("OOF supervision required for scale initialization")
    weights = 2 * batch.weights[mask]
    if not torch.isclose(weights.sum(), weights.new_tensor(1), rtol=0, atol=1e-14):
        raise ValueError("OOF weights must sum to one")
    base = batch.baseline[batch.row_batch, batch.row_lead][mask]
    value = ((base - batch.targets[mask]).square() * weights[:, None]).sum(dim=0)
    return np.maximum(0.002, torch.sqrt(value).numpy())


def training_inputs(spec, h, labels, pool):
    if labels.shape != (h, 4):
        raise ValueError("Trainer requires the exact observation prefix")
    table = query_table(h)
    saved = pd.read_csv(
        ROOT / spec["direct_source"] / f"queries_{h}.csv",
        float_precision="round_trip",
    )
    pd.testing.assert_frame_equal(table, saved, check_exact=True)
    batch = make_batch(table, pool, labels, saved_scalers(spec, h), "teacher_OOF", True)
    context = row_context(table, pool, labels, "teacher_OOF")
    torch.testing.assert_close(
        context.baseline, batch.baseline[batch.row_batch, batch.row_lead],
        rtol=0, atol=0,
    )
    return table, batch, context, initial_scale(batch)


def evaluation_inputs(spec, h, labels, pool):
    base, physical = pool[h]
    table, batch = evaluation_batch(base, physical, labels, h, saved_scalers(spec, h))
    saved = pd.read_csv(ROOT / spec["direct_source"] / f"evaluation_queries_{h}.csv")
    pd.testing.assert_frame_equal(table, saved, check_exact=True)
    full_table = pd.concat(
        [pd.DataFrame(dict(origin=[30], target=[30], teacher=[h])), table],
        ignore_index=True,
    )
    if full_table.target.tolist() != list(range(30, h + 180)):
        raise ValueError("Evaluation must cover every valid day exactly once")
    context = row_context(full_table, pool, labels, "teacher")
    return table, batch, context


def new_models(seed, sigma0):
    setup(seed)
    mean = DirectConvLSTM()
    setup(seed)
    scale = ConditionalScale(sigma0)
    return mean, scale


def probability_loss(batch, context, mean, scale, detach_mean):
    probability_mean = mean.detach() if detach_mean else mean
    sigma = scale(context.features(probability_mean))
    mask = batch.blocks == 1
    values = (
        torch.log(sigma[mask] / 100)
        + 0.5 * ((batch.targets[mask] - probability_mean[mask]) / sigma[mask]).square()
    ).mean(dim=1)
    loss = (values * 2 * batch.weights[mask]).sum()
    if not torch.isfinite(loss) or not torch.isfinite(sigma).all():
        raise ArithmeticError("Nonfinite Gaussian NLL or scale")
    return loss


def assign_gradients(parameters, flat):
    start = 0
    for parameter in parameters:
        end = start + parameter.numel()
        parameter.grad = torch.from_numpy(flat[start:end].reshape(parameter.shape).copy())
        start = end
    if start != len(flat):
        raise ValueError("Gradient layout differs from parameters")


def joint_step(mean, scale, mean_op, scale_op, batch, context, detach_mean, before_step=None):
    """Both gradients use one forward; only detach_mean changes the two arms."""
    mean_op.zero_grad(set_to_none=True)
    scale_op.zero_grad(set_to_none=True)
    correction = mean(batch.encoder, batch.decoder)
    losses = block_losses(batch, correction)
    parameters = tuple(mean.parameters())
    scale_parameters = tuple(scale.parameters())
    block_gradients = []
    for b in (0, 1):
        values = torch.autograd.grad(losses[b], parameters, retain_graph=True)
        block_gradients.append(torch.cat([v.reshape(-1) for v in values]).numpy())
    record, mixed = profile(losses.detach().numpy(), np.stack(block_gradients))
    nll = probability_loss(
        batch, context, selected_mean(batch, correction), scale, detach_mean
    )
    requested = scale_parameters if detach_mean else parameters + scale_parameters
    gradients = torch.autograd.grad(nll, requested)
    if detach_mean:
        probability_gradient = np.zeros_like(mixed)
        scale_gradients = gradients
    else:
        probability_gradient = torch.cat(
            [v.reshape(-1) for v in gradients[:len(parameters)]]
        ).numpy()
        scale_gradients = gradients[len(parameters):]
    combined = mixed + probability_gradient
    assign_gradients(parameters, combined)
    for parameter, gradient in zip(scale_parameters, scale_gradients):
        parameter.grad = gradient.detach().clone()
    record.update(
        nll=float(nll.detach()),
        total_loss=record["balanced_loss"] + float(nll.detach()),
        nll_mean_gradient_norm=float(np.linalg.norm(probability_gradient)),
        mean_gradient_norm=float(np.linalg.norm(combined)),
        scale_gradient_norm=float(torch.linalg.vector_norm(
            torch.cat([g.reshape(-1) for g in scale_gradients])
        )),
    )
    if before_step:
        before_step("mean")
    record["mean_clipped_from"] = update(mean, mean_op)
    if before_step:
        before_step("scale")
    record["scale_clipped_from"] = update(scale, scale_op)
    if not all(np.isfinite(float(v)) for v in record.values()):
        raise ArithmeticError("Nonfinite training record")
    return record


def objectives(mean, scale, batch, context, detach_mean):
    with torch.no_grad():
        correction = mean(batch.encoder, batch.decoder)
        values = block_losses(batch, correction)
        nll = probability_loss(
            batch, context, selected_mean(batch, correction), scale, detach_mean
        )
    return dict(anchor_mse=float(values[0]), oof_mse=float(values[1]), nll=float(nll))


def predict_distribution(mean, scale, batch, context, h):
    with torch.no_grad():
        correction = mean(batch.encoder, batch.decoder)
        mu = torch.cat((context.baseline[:1], selected_mean(batch, correction)))
        sigma = scale(context.features(mu))
    if mu.shape != (h + 150, 4) or sigma.shape != mu.shape:
        raise ValueError("Incomplete valid evaluation days")
    if not torch.isfinite(mu).all() or not torch.isfinite(sigma).all() or (sigma <= 0.001).any():
        raise ArithmeticError("Nonfinite or collapsed predictive distribution")
    full_mu = np.full((h + 180, 4), np.nan)
    full_sigma = np.full_like(full_mu, np.nan)
    full_mu[30:], full_sigma[30:] = mu.numpy(), sigma.numpy()
    return full_mu, full_sigma


def load_distribution(out, spec, h, strategy):
    if strategy == "P0":
        path = ROOT / spec["reference_source"] / f"prediction_{h}_P0.npz"
    elif strategy == "DIRECT_V2_0":
        path = ROOT / spec["direct_source"] / f"prediction_{h}_DIRECT.npz"
    elif strategy in ARMS:
        path = Path(out) / f"prediction_{h}_{strategy}.npz"
    else:
        raise ValueError("Unregistered distribution")
    with np.load(path, allow_pickle=False) as bundle:
        means = bundle["means"]
        sigmas = (
            np.broadcast_to(bundle["scales"][:, None], means.shape).copy()
            if strategy == "P0" else bundle["sigmas"]
        )
    components = 1 if strategy == "P0" else 3
    if means.shape != (components, h + 180, 4) or sigmas.shape != means.shape:
        raise ValueError("Reference component or neural seed count differs")
    if not np.isfinite(means[:, 30:]).all() or not np.isfinite(sigmas[:, 30:]).all():
        raise ValueError("Incomplete valid predictions")
    return means, sigmas


def decide(metrics, spec):
    keys = ["outer_days", "strategy", "part", "station"]
    expected = {
        (h, s, p, j) for h in (432, 612) for s in STRATEGIES
        for p in ("train", "prediction") for j in POINTS
    }
    if len(metrics) != len(expected) or set(map(tuple, metrics[keys].values)) != expected:
        raise ValueError("Exactly 64 unique point/phase/strategy rows required")
    numeric = metrics.select_dtypes(include="number")
    if not np.isfinite(numeric).all().all():
        raise ValueError("Finite metrics required")
    tol, ctol = spec["strict_tolerance_mm"], spec["coverage_tolerance"]
    indexed = metrics.set_index(keys).sort_index()
    points, windows = [], []
    for h in (432, 612):
        per_window = {"outer_days": h}
        averages = {
            (s, p): indexed.loc[(h, s, p)].mean(numeric_only=True)
            for s in STRATEGIES for p in ("train", "prediction")
        }
        for metric in ("rmse_mm", "mae_mm"):
            per_window[f"fit_{metric}_better_P0"] = bool(
                averages["JOINT", "train"][metric] < averages["P0", "train"][metric] - tol
            )
        for reference in ("P0", "DETACHED"):
            for metric in ("rmse_mm", "mae_mm", "crps_mm", "interval_score_90_mm"):
                per_window[f"prediction_{metric}_better_{reference}"] = bool(
                    averages["JOINT", "prediction"][metric]
                    < averages[reference, "prediction"][metric] - tol
                )
        for j in POINTS:
            a = indexed.loc[(h, "JOINT", "prediction", j)]
            b = indexed.loc[(h, "P0", "prediction", j)]
            da, db = abs(a.coverage_90 - .9), abs(b.coverage_90 - .9)
            row = dict(outer_days=h, station=j)
            for reference in ("P0", "DETACHED", "DIRECT_V2_0"):
                for phase in ("train", "prediction"):
                    new = indexed.loc[(h, "JOINT", phase, j)]
                    old = indexed.loc[(h, reference, phase, j)]
                    for metric in ("rmse_mm", "mae_mm", "crps_mm", "interval_score_90_mm"):
                        difference = float(new[metric] - old[metric])
                        row[f"{phase}_{metric}_minus_{reference}"] = difference
                        row[f"{phase}_{metric}_relative_gain_{reference}"] = (
                            -difference / float(old[metric]) if old[metric] else None
                        )
            row["rmse_guard"] = bool(a.rmse_mm <= b.rmse_mm + tol)
            row["crps_guard"] = bool(a.crps_mm <= b.crps_mm + tol)
            row["coverage_guard"] = bool(da <= db + spec["coverage_slack"] + ctol)
            row["width_guard"] = bool(
                a.width_90_mm <= b.width_90_mm + tol
                or (da < db - ctol and a.interval_score_90_mm < b.interval_score_90_mm - tol)
            )
            row["strict_mean_improvement"] = all(
                row[f"{p}_{m}_minus_P0"] < -tol
                for p in ("train", "prediction") for m in ("rmse_mm", "mae_mm")
            )
            row["point_guards_passed"] = all(
                row[k] for k in ("rmse_guard", "crps_guard", "coverage_guard", "width_guard")
            )
            points.append(row)
        per_window["all_point_guards"] = all(
            r["point_guards_passed"] for r in points if r["outer_days"] == h
        )
        per_window["passed"] = all(v for k, v in per_window.items() if k != "outer_days")
        windows.append(per_window)
    return dict(
        candidate="JOINT", passed=all(w["passed"] for w in windows),
        strict_mean_improvement_count=sum(r["strict_mean_improvement"] for r in points),
        point_windows=points, windows=windows, stop_after_this_run=True,
        interpretation="exploratory two-window evidence; no automatic final-window training",
    )


def score_saved(out, spec, labels, dates):
    """Pure scoring of locked arrays: no optimizer, physics or model selection."""
    if labels.shape != (792, 4) or len(dates) != 792:
        raise ValueError("Exactly the two registered windows are required")
    rows, seeds, daily, aggregates = [], [], [], []
    for h in spec["outer_days"]:
        for strategy in STRATEGIES:
            means, sigmas = load_distribution(out, spec, h, strategy)
            y = labels[:h + 180]
            records, summary = score(means, sigmas, y, h, strategy)
            rows.extend(records)
            values = crps(means[:, 30:], sigmas[:, 30:], y[30:])
            for seed in ([] if strategy == "P0" else spec["seeds"]):
                seed_rows, _ = score(
                    means[seed:seed + 1], sigmas[seed:seed + 1], y, h, strategy
                )
                seeds.extend(dict(seed=seed, **r) for r in seed_rows)
            for phase, start, end in (("train", 30, h), ("prediction", h, h + 180)):
                point_rows = pd.DataFrame([r for r in records if r["part"] == phase])
                avg = point_rows.drop(columns=["outer_days", "days"]).mean(numeric_only=True)
                common = dict(outer_days=h, strategy=strategy, part=phase,
                              days=end - start, point_days=(end - start) * 4)
                aggregates.append(dict(**common, aggregation="point_mean", **avg.to_dict()))
                pooled = avg.to_dict()
                pooled["rmse_mm"] = float(np.sqrt(np.mean(
                    (summary["mean"][start - 30:end - 30] - y[start:end]) ** 2
                )))
                aggregates.append(dict(**common, aggregation="pooled", **pooled))
            for j, station in enumerate(POINTS):
                frame = pd.DataFrame(dict(
                    outer_days=h, strategy=strategy, station=station,
                    day=np.arange(30, h + 180),
                    date=pd.DatetimeIndex(dates[30:h + 180]).strftime("%Y-%m-%d"),
                    part=np.where(np.arange(30, h + 180) < h, "train", "prediction"),
                    observed_mm=y[30:, j], mean_mm=summary["mean"][:, j],
                    error_mm=summary["mean"][:, j] - y[30:, j], crps_mm=values[:, j],
                ))
                for level in (80, 90, 95):
                    lower, upper = (summary[f"{e}_{level}"][:, j] for e in ("lower", "upper"))
                    frame[f"lower_{level}_mm"] = lower
                    frame[f"upper_{level}_mm"] = upper
                    frame[f"interval_score_{level}_mm"] = interval_score(y[30:, j], lower, upper, level)
                daily.append(frame)
    metrics = pd.DataFrame(rows)
    return {
        "metrics.csv": metrics,
        "seed_metrics.csv": pd.DataFrame(seeds),
        "aggregate_metrics.csv": pd.DataFrame(aggregates),
        "daily_predictions.csv": pd.concat(daily, ignore_index=True),
        "comparison.csv": pd.DataFrame(decide(metrics, spec)["point_windows"]),
    }


def reference_metric_error(metrics, spec):
    maximum = 0.0
    for strategy, source, source_strategy in (
        ("P0", "reference_source", "P0"), ("DIRECT_V2_0", "direct_source", "DIRECT")
    ):
        old = pd.read_csv(ROOT / spec[source] / "metrics.csv", float_precision="round_trip")
        old = old[old.strategy == source_strategy].copy()
        old["strategy"] = strategy
        keys = ["outer_days", "strategy", "part", "station"]
        new = metrics[metrics.strategy == strategy].set_index(keys).sort_index()
        old = old.set_index(keys).sort_index().loc[new.index, new.columns]
        np.testing.assert_allclose(new.values, old.values, rtol=0, atol=spec["score_atol_mm"])
        maximum = max(maximum, float(np.max(np.abs(new.values - old.values))))
    return maximum
