"""Bounded experiment runner; every rolling forecast precedes target release."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import time

import numpy as np
import pandas as pd
import torch

from physics_guided.reference import ROOT, save_json, sha
from .data import (
    ObservationStream,
    array_sha,
    example,
    load_teachers,
    prepare_teachers,
    read_prefix,
    select_teacher,
    training_examples,
)
from .models import Scaling, new_model, objective, predict
from .scoring import CausalCalibration, aggregate, gate, score_predictions


def utc():
    return datetime.now(timezone.utc).isoformat()


class Recorder:
    def __init__(self, out):
        self.out = Path(out)
        self.started = time.monotonic()
        self.chain = ""

    def event(self, kind, **fields):
        event = dict(
            kind=kind,
            utc=utc(),
            elapsed_seconds=time.monotonic() - self.started,
            **fields,
        )
        event["previous_sha256"] = self.chain
        raw = json.dumps(event, sort_keys=True, allow_nan=False)
        import hashlib

        self.chain = hashlib.sha256(raw.encode()).hexdigest()
        with (self.out / "events.jsonl").open("a") as f:
            f.write(raw + "\n")
        if kind not in ("forecast_locked", "observation_released"):
            print(json.dumps(event, ensure_ascii=False), flush=True)


def source_snapshot(out, config, pool_dir):
    paths = [
        Path(config),
        ROOT / "docs/ootang_rolling_probability_plan.v3.0.md",
        ROOT / "data/monitoring_data.csv",
        ROOT / "code/physics_guided/reference.py",
    ]
    paths += sorted((ROOT / "code/rolling_probability").glob("*.py"))
    config_values = json.loads(Path(config).read_text())
    if config_values.get("candidate_plan"):
        paths.append(ROOT / config_values["candidate_plan"])
    paths += [ROOT / p for p in config_values.get("additional_sources", ())]
    if config_values.get("reuse_development"):
        prior = ROOT / config_values["reuse_development"]["path"]
        paths += [prior / "artifact_manifest.json", prior / "internal_selection.json"]
        paths += sorted((prior / "development_training").glob("*"))
        paths += sorted((prior / "development").glob("*.npz"))
    paths += sorted((ROOT / "tests").glob("test_rolling_*.py"))
    paths += sorted(Path(pool_dir).glob("*.json")) + sorted(
        Path(pool_dir).glob("*.npz")
    )
    paths = [p.resolve() for p in paths]
    records = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    for p in paths:
        if p.suffix in (".py", ".json", ".md"):
            target = Path(out) / "sources" / p.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
    save_json(
        Path(out) / "sources.json",
        dict(
            git_head=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            files=records,
            python=platform.python_version(),
            torch=torch.__version__,
            numpy=np.__version__,
        ),
    )
    return records


def train(labels, pool, spec, out, steps, checkpoints, recorder):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    data = training_examples(
        labels,
        pool,
        spec["horizons"],
        spec["history_days"],
        spec.get("extra_baselines", ()),
    )
    scaling = Scaling(data)
    save_json(out / "scaling.json", scaling.state())
    np.savez_compressed(
        out / "training_queries.npz",
        origins=data["origins"],
        teachers=data["teachers"],
        target_last=data["origins"] + spec["horizons"] - 1,
    )
    save_json(
        out / "data_contract.json",
        dict(
            label_rows=len(labels),
            max_label_index=len(labels) - 1,
            label_sha256=array_sha(labels),
            samples=len(data["origins"]),
            input_channels=data["x"].shape[2],
            future_channels=data["z"].shape[2],
            max_target_index=int((data["origins"] + spec["horizons"] - 1).max()),
            teacher_never_later_than_origin=bool(
                np.all(data["teachers"] <= data["origins"])
            ),
            preprocessing_fitted_on_current_training_examples=True,
        ),
    )
    x, z = scaling.transform(data["x"], data["z"])
    x, z = torch.from_numpy(x), torch.from_numpy(z)
    target = torch.from_numpy(
        ((data["y"] - data["anchor"]) / scaling.target_scale).astype(np.float32)
    )
    experts = None
    if spec.get("expert_names"):
        raw = np.stack([data["baselines"][k] for k in spec["expert_names"]], axis=2)
        experts = torch.from_numpy(
            (
                (raw - data["anchor"][:, :, None, :])
                / scaling.target_scale[None, :, None, :]
            ).astype(np.float32)
        )
    saved = {step: [] for step in checkpoints}
    logs = []
    for seed in spec["seeds"]:
        torch.manual_seed(seed)
        rng = np.random.default_rng(seed)
        model = new_model(x.shape[2], z.shape[2], spec)
        optimizer = torch.optim.Adam(
            model.parameters(),
            lr=spec["learning_rate"],
            weight_decay=spec["weight_decay"],
        )
        for step in range(1, steps + 1):
            model.train()
            ids = rng.integers(0, len(x), size=spec["batch_size"])
            optimizer.zero_grad(set_to_none=True)
            mu, sd = model(x[ids], z[ids], None if experts is None else experts[ids])
            loss = objective(mu, sd, target[ids], spec["mse_weight"])
            if not torch.isfinite(loss):
                raise ArithmeticError("Training loss is nonfinite")
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_(
                model.parameters(), spec["gradient_clip"], error_if_nonfinite=True
            )
            optimizer.step()
            if step == 1 or step % 25 == 0:
                record = dict(
                    seed=seed,
                    update=step,
                    loss=float(loss.detach()),
                    gradient_norm=float(norm),
                )
                logs.append(record)
                with (out / "training.jsonl").open("a") as f:
                    f.write(json.dumps(record) + "\n")
            if step in checkpoints:
                path = out / f"seed{seed}_u{step}.pt"
                torch.save(
                    dict(
                        state_dict=model.state_dict(),
                        x_channels=x.shape[2],
                        z_channels=z.shape[2],
                        hidden=spec["hidden_channels"],
                        seed=seed,
                        updates=step,
                    ),
                    path,
                )
                copied = new_model(x.shape[2], z.shape[2], spec)
                copied.load_state_dict(
                    torch.load(path, weights_only=True)["state_dict"]
                )
                copied.eval()
                saved[step].append(copied)
                recorder.event(
                    "training_checkpoint_locked",
                    label_rows=len(labels),
                    seed=seed,
                    updates=step,
                    path=str(path.relative_to(ROOT)),
                    sha256=sha(path),
                    loss=float(loss.detach()),
                )
    baseline_scale = {
        k: np.maximum(0.01, np.sqrt(np.mean((data["y"] - v) ** 2, axis=0)))
        for k, v in data["baselines"].items()
    }
    save_json(
        out / "baseline_scales.json", {k: v.tolist() for k, v in baseline_scale.items()}
    )
    return saved, scaling, baseline_scale


def load_group(train_dir, spec, step):
    models = []
    for seed in spec["seeds"]:
        d = torch.load(Path(train_dir) / f"seed{seed}_u{step}.pt", weights_only=True)
        model = new_model(d["x_channels"], d["z_channels"], spec)
        model.load_state_dict(d["state_dict"])
        model.eval()
        models.append(model)
    scaling = Scaling(state=json.loads((Path(train_dir) / "scaling.json").read_text()))
    scales = {
        k: np.array(v)
        for k, v in json.loads(
            (Path(train_dir) / "baseline_scales.json").read_text()
        ).items()
    }
    return models, scaling, scales


def forecast_phase(
    pool, spec, start, end, groups, scaling, base_scales, out, recorder, save_seeds=True
):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    H = spec["horizons"]
    names = list(base_scales) + list(groups)
    if save_seeds:
        names += [f"{g}_seed{s}" for g in groups for s in spec["seeds"]]
    shape = (end - start, H, 4)
    records = {
        name: dict(
            origins=np.arange(start, end),
            mean=np.full(shape, np.nan),
            raw_sigma=np.full(shape, np.nan),
            sigma=np.full(shape, np.nan),
            calibration_factor=np.full(shape, np.nan),
        )
        for name in names
    }
    prob = spec["probability"]
    calibrators = {
        name: CausalCalibration(
            H,
            prob["window"],
            prob["prior_count"],
            prob["prior_sum_squares"],
            feedback=prob.get("feedback"),
        )
        for name in names
    }
    if prob.get("feedback"):
        for record in records.values():
            record["feedback_log_scale"] = np.full(shape, np.nan)
    stream = ObservationStream(ROOT / spec["data"], start, end)
    recorder.event(
        "forecast_phase_started",
        start=start,
        end=end,
        weight_training_last_index=start - 1,
        models=names,
    )
    for n in range(start, end):
        if len(stream.history) != n:
            raise ValueError("Observation stream crossed forecast origin")
        teacher = select_teacher(pool, n)
        x, z, base = example(
            stream.history,
            teacher,
            H,
            spec["history_days"],
            spec.get("extra_baselines", ()),
        )
        valid = min(len(z), end - n)
        z = z[:valid]
        base = {k: v[:valid] for k, v in base.items()}
        forecasts = {k: (base[k], base_scales[k][:valid]) for k in base_scales}
        expert_means = None
        if spec.get("expert_names"):
            expert_means = np.stack([base[k] for k in spec["expert_names"]], axis=1)[
                None
            ]
        for name, models in groups.items():
            mean, sd, seed_mu, seed_sd = predict(
                models, scaling, x[None], z[None], base["B_ANCHOR"][None], expert_means
            )
            forecasts[name] = (mean[0], sd[0])
            if save_seeds:
                for j, seed in enumerate(spec["seeds"]):
                    forecasts[f"{name}_seed{seed}"] = (seed_mu[j, 0], seed_sd[j, 0])
        for name, (mean, raw_sd) in forecasts.items():
            factors = calibrators[name].factors(n)[:valid]
            sd = np.maximum(prob["sigma_floor_mm"], raw_sd * factors)
            if (
                mean.shape != (valid, 4)
                or not np.isfinite(mean).all()
                or not np.isfinite(sd).all()
            ):
                raise ArithmeticError("Invalid forecast")
            r = records[name]
            row = n - start
            if prob.get("feedback"):
                r["feedback_log_scale"][row, :valid] = calibrators[name].log_scale[
                    :valid
                ]
            for key, value in (
                ("mean", mean),
                ("raw_sigma", raw_sd),
                ("sigma", sd),
                ("calibration_factor", factors),
            ):
                r[key][row, :valid] = value
        recorder.event(
            "forecast_locked",
            origin=n,
            available_label_last_index=n - 1,
            teacher_prefix=teacher.prefix,
            valid_horizons=valid,
            history_sha256=array_sha(stream.history),
            predictions={
                name: array_sha(
                    np.stack(
                        [
                            r["mean"][n - start, :valid],
                            r["sigma"][n - start, :valid],
                            r["raw_sigma"][n - start, :valid],
                        ]
                    )
                )
                for name, r in records.items()
            },
        )
        value = stream.release()
        recorder.event("observation_released", index=n, value_sha256=array_sha(value))
        # The target at n has now matured, and can affect origin n+1 only.
        for past in range(max(start, n - H + 1), n + 1):
            k = n - past
            for name, r in records.items():
                calibrators[name].update(
                    k,
                    value - r["mean"][past - start, k],
                    r["raw_sigma"][past - start, k],
                    n,
                    n + 1,
                    issued_sigma=r["sigma"][past - start, k],
                )
        if (n - start + 1) % 50 == 0:
            print(f"rolling {start}:{end} origin {n + 1}/{end}", flush=True)
    recorder.event("all_rolling_forecasts_locked", start=start, end=end)
    if prob.get("feedback"):
        save_json(
            out / "feedback_state.json",
            {name: c.feedback_summary() for name, c in calibrators.items()},
        )
    tables = []
    for name, r in records.items():
        r["teacher_prefixes"] = np.array(
            [select_teacher(pool, n).prefix for n in r["origins"]]
        )
        np.savez_compressed(out / f"{name}.npz", **r)
        table = score_predictions(r, stream.history, prob["levels"])
        table.insert(0, "model", name)
        if name == "B_RAW":
            table.loc[
                :,
                [
                    k
                    for k in table
                    if k not in ("model", "horizon", "point", "n", "mae", "rmse")
                ],
            ] = np.nan
        tables.append(table)
    metrics = pd.concat(tables, ignore_index=True)
    summary = aggregate(metrics)
    metrics.to_csv(out / "metrics.csv", index=False, float_format="%.12g")
    summary.to_csv(out / "summary.csv", index=False, float_format="%.12g")
    save_json(
        out / "manifest.json",
        dict(
            start=start,
            end=end,
            files={p.name: sha(p) for p in out.iterdir() if p.is_file()},
            label_rows_released=end - start,
            prediction_origins=end - start,
            mean_is_rolling=True,
            probability_extension_baselines=[
                k + "_G" for k in base_scales if k != "B_RAW"
            ],
            table_baseline_names_omit_G_suffix=True,
            B_RAW_probability="not applicable; stored sigma is an unused computational placeholder",
        ),
    )
    recorder.event("phase_scored", start=start, end=end, metrics_rows=len(metrics))
    return metrics, summary


def develop(pool, spec, out, recorder):
    inner_start, inner_end = spec["stages"]["inner"]
    dev_start, dev_end = spec["stages"]["development"]
    labels = read_prefix(ROOT / spec["data"], inner_start)
    recorder.event("training_prefix_read", rows=len(labels))
    groups, scaling, base_scales = train(
        labels,
        pool,
        spec,
        out / "inner_training",
        max(spec["checkpoints"]),
        spec["checkpoints"],
        recorder,
    )
    prefix = spec["candidate"]
    models = {f"{prefix}_u{k}": v for k, v in groups.items()}
    _, summary = forecast_phase(
        pool,
        spec,
        inner_start,
        inner_end,
        models,
        scaling,
        base_scales,
        out / "inner",
        recorder,
    )
    s = summary[summary.horizon == spec["primary_horizon"]].set_index("model")
    denominator = s.loc["B_ANCHOR"]
    scores = {
        k: float(
            s.loc[f"{prefix}_u{k}", "rmse"] / denominator.rmse
            + s.loc[f"{prefix}_u{k}", "crps"] / denominator.crps
        )
        for k in spec["checkpoints"]
    }
    selected = min(scores, key=lambda k: (scores[k], k))
    save_json(
        out / "internal_selection.json",
        dict(
            checkpoint_scores=scores,
            selected_updates=selected,
            criterion="30-day mean RMSE ratio + CRPS ratio vs anchored B+",
            tie="earlier update",
        ),
    )
    recorder.event("internal_selection_locked", updates=selected, scores=scores)
    labels = read_prefix(ROOT / spec["data"], dev_start)
    recorder.event("training_prefix_read", rows=len(labels))
    groups, scaling, base_scales = train(
        labels, pool, spec, out / "development_training", selected, [selected], recorder
    )
    metrics, summary = forecast_phase(
        pool,
        spec,
        dev_start,
        dev_end,
        {prefix: groups[selected]},
        scaling,
        base_scales,
        out / "development",
        recorder,
    )
    s = summary[summary.horizon == spec["primary_horizon"]].set_index("model")
    simple = min(
        ("B_TREND14", "PERSIST", "DRIFT14", *spec.get("extra_baselines", ())),
        key=lambda name: s.loc[name, "rmse"],
    )
    decision = gate(metrics, summary, prefix, simple, spec)
    decision["selected_updates"] = selected
    save_json(out / "decision.json", decision)
    recorder.event("development_decision", **decision)
    return decision


def transfer(pool, spec, out, recorder, development):
    prior = Path(development).resolve()
    decision = json.loads((prior / "decision.json").read_text())
    if not decision["passed"]:
        raise ValueError(
            "Selected development candidate has not passed; no automatic transfer"
        )
    if spec["candidate"] != decision["candidate"]:
        raise ValueError("Transfer candidate identity changed")
    start, end = spec["stages"]["transfer"]
    step = decision["selected_updates"]
    save_json(
        out / "transfer_lock.json",
        dict(
            development_path=str(prior.relative_to(ROOT)),
            development_decision_sha256=sha(prior / "decision.json"),
            candidate=spec["candidate"],
            selected_updates=step,
            simple_baseline=decision["simple_baseline"],
            locked_utc=utc(),
        ),
    )
    labels = read_prefix(ROOT / spec["data"], start)
    recorder.event("training_prefix_read", rows=len(labels))
    groups, scaling, base_scales = train(
        labels, pool, spec, out / "transfer_training", step, [step], recorder
    )
    metrics, summary = forecast_phase(
        pool,
        spec,
        start,
        end,
        {spec["candidate"]: groups[step]},
        scaling,
        base_scales,
        out / "transfer",
        recorder,
    )
    result = gate(
        metrics, summary, spec["candidate"], decision["simple_baseline"], spec
    )
    result["selected_updates"] = step
    save_json(out / "decision.json", result)
    recorder.event("transfer_decision", **result)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("prepare", "develop", "transfer"))
    ap.add_argument(
        "--config", default=str(ROOT / "config/ootang_rolling_probability.v3_0.json")
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--pool")
    ap.add_argument("--end", type=int, default=1168)
    ap.add_argument("--development")
    args = ap.parse_args()
    spec = json.loads(Path(args.config).read_text())
    if sha(ROOT / spec["data"]) != spec["data_sha256"]:
        raise ValueError("Frozen observation data changed")
    remaining = (
        datetime.fromisoformat(spec["deadline_utc"]) - datetime.now(timezone.utc)
    ).total_seconds()
    if remaining <= 0:
        raise TimeoutError("The authorized overall time budget has expired")

    def timeout(_signum, _frame):
        raise TimeoutError("Rolling experiment hard time limit")

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(int(min(remaining, spec["run_timeout_seconds"])))
    torch.set_num_threads(spec["cpu_threads"])
    torch.use_deterministic_algorithms(True)
    out = Path(args.out).resolve()
    if args.action == "prepare":
        result = prepare_teachers(spec, out, args.end)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    recorder = Recorder(out)
    status = dict(
        action=args.action,
        pid=os.getpid(),
        started_utc=utc(),
        state="running",
        deadline=spec["deadline_utc"],
    )
    save_json(out / "status.json", status)
    try:
        sources = source_snapshot(out, args.config, args.pool)
        pool = load_teachers(args.pool)
        if spec.get("model_family") == "ridge_dynamic":
            from . import ridge

            if args.action == "develop":
                ridge.develop(pool, spec, out, recorder)
            else:
                ridge.transfer(pool, spec, out, recorder, args.development)
        elif args.action == "develop":
            develop(pool, spec, out, recorder)
        else:
            transfer(pool, spec, out, recorder, args.development)
        for name, expected in sources.items():
            if sha(ROOT / name) != expected:
                raise ValueError("Source changed during run: " + name)
        status.update(
            state="completed",
            finished_utc=utc(),
            elapsed_seconds=time.monotonic() - recorder.started,
            exit_code=0,
        )
    except BaseException as exc:
        status.update(
            state="failed",
            finished_utc=utc(),
            elapsed_seconds=time.monotonic() - recorder.started,
            error=repr(exc),
            exit_code=1,
        )
        save_json(out / "status.json", status)
        raise
    save_json(out / "status.json", status)
    save_json(
        out / "artifact_manifest.json",
        dict(
            files={
                str(p.relative_to(out)): sha(p) for p in out.rglob("*") if p.is_file()
            }
        ),
    )


if __name__ == "__main__":
    main()
