"""Block gradients, local geometry, and finite differences on parameter copies."""

import copy
from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch


@dataclass
class Budget:
    gradient_limit: int
    difference_limit: int
    gradient_calls: int = 0
    difference_calls: int = 0
    backward_calls: int = 0

    def forward(self, phase):
        if phase not in ("gradient", "difference"):
            raise ValueError("Unknown diagnostic phase")
        key = phase + "_calls"
        if getattr(self, key) >= getattr(self, phase + "_limit"):
            raise RuntimeError("Registered network call budget exhausted")
        setattr(self, key, getattr(self, key) + 1)

    def backward(self):
        if self.backward_calls >= self.gradient_calls:
            raise RuntimeError("A backward call requires an unconsumed forward")
        self.backward_calls += 1

    def record(self):
        return dict(
            gradient_calls=self.gradient_calls,
            difference_calls=self.difference_calls,
            forward_calls=self.gradient_calls + self.difference_calls,
            backward_calls=self.backward_calls,
        )


def layout(model):
    result, start = [], 0
    for name, parameter in model.named_parameters():
        if parameter.dtype != torch.float64 or not torch.isfinite(parameter).all():
            raise ValueError("Finite float64 model parameters required")
        stop = start + parameter.numel()
        result.append(
            dict(name=name, shape=list(parameter.shape), start=start, stop=stop)
        )
        start = stop
    return result


def validate_samples(samples, batch):
    n = len(samples.blocks)
    if type(batch) is not int or batch < 1:
        raise ValueError("Positive integer batch required")
    if (
        samples.base.shape != (n, 4)
        or samples.target.shape != (n, 4)
        or samples.weights.shape != (n,)
        or len(samples.x) != n
        or set(samples.blocks) != {0, 1}
    ):
        raise ValueError("Two nonempty four-point blocks required")
    for value in (samples.x, samples.base, samples.target, samples.weights):
        if value.dtype != torch.float64 or not torch.isfinite(value).all():
            raise ValueError("Finite float64 samples required")
    if not (samples.weights > 0).all():
        raise ValueError("Positive sample weights required")
    for block in (0, 1):
        mass = float(samples.weights[samples.blocks == block].sum())
        if abs(mass - 0.5) > 1e-14:
            raise ValueError("Frozen blocks must each have mass one half")


def block_gradients(model, samples, batch, budget):
    validate_samples(samples, batch)
    parameters = tuple(model.parameters())
    losses, gradients = [], []
    for block in (0, 1):
        positions = np.flatnonzero(samples.blocks == block)
        accum = [torch.zeros_like(p) for p in parameters]
        total = 0.0
        for start in range(0, len(positions), batch):
            ids = positions[start : start + batch]
            budget.forward("gradient")
            residual = (
                samples.base[ids] + model(samples.x[ids]) - samples.target[ids]
            ) / 100
            loss = (residual.square() * (2 * samples.weights[ids, None])).sum() / 4
            if not torch.isfinite(loss):
                raise ArithmeticError("Nonfinite block loss")
            budget.backward()
            for destination, value in zip(accum, torch.autograd.grad(loss, parameters)):
                destination.add_(value)
            total += float(loss.detach())
        gradient = torch.cat([a.flatten() for a in accum]).numpy()
        if not np.isfinite(gradient).all():
            raise ArithmeticError("Nonfinite block gradient")
        losses.append(total)
        gradients.append(gradient)
    return np.array(losses), np.array(gradients)


def block_values(model, samples, batch, budget):
    values = []
    with torch.no_grad():
        for block in (0, 1):
            positions = np.flatnonzero(samples.blocks == block)
            total = 0.0
            for start in range(0, len(positions), batch):
                ids = positions[start : start + batch]
                budget.forward("difference")
                residual = (
                    samples.base[ids] + model(samples.x[ids]) - samples.target[ids]
                ) / 100
                total += float(
                    (residual.square().mean(1) * samples.weights[ids] * 2).sum()
                )
            values.append(total)
    return np.array(values)


def descent_direction(gradients, tolerance=1e-12):
    total = gradients.mean(axis=0)
    norm = np.linalg.norm(total)
    return None if norm <= tolerance else -total / norm


def finite_differences(
    model, samples, direction, steps, batch, budget, evaluate=block_values
):
    if direction is None:
        return np.full((len(steps), 2, 2), np.nan)
    if not np.isfinite(direction).all() or not np.isclose(np.linalg.norm(direction), 1):
        raise ValueError("A finite unit direction is required")
    if any(not np.isfinite(e) or e <= 0 for e in steps):
        raise ValueError("Positive finite perturbation sizes required")
    probe = copy.deepcopy(model)
    parameters = tuple(probe.parameters())
    original = torch.nn.utils.parameters_to_vector(parameters).detach().clone()
    if len(direction) != len(original):
        raise ValueError("Parameter direction has the wrong dimension")
    vector = torch.as_tensor(direction)
    values = []
    for epsilon in steps:
        pair = []
        for sign in (-1, 1):
            with torch.no_grad():
                torch.nn.utils.vector_to_parameters(
                    original + sign * epsilon * vector, parameters
                )
            pair.append(evaluate(probe, samples, batch, budget))
        values.append(pair)
    return np.array(values)


def geometry(a, p, spec):
    if a.shape != p.shape or not np.isfinite(a).all() or not np.isfinite(p).all():
        raise ValueError("Matching finite gradients required")
    na, np_, nt = (float(np.linalg.norm(v)) for v in (a, p, (a + p) / 2))
    za, zp, zt = (n <= spec["zero_norm_tolerance"] for n in (na, np_, nt))
    dot = float(a @ p)
    ratio = np.nan if za else np_ / na
    cosine = np.nan if za or zp else dot / (na * np_)
    direction = None if zt else -(a + p) / (2 * nt)
    da = np.nan if zt else float(a @ direction)
    dp = np.nan if zt else float(p @ direction)
    return dict(
        anchor_norm=na,
        paired_norm=np_,
        total_norm=nt,
        norm_ratio=ratio,
        dot=dot,
        cosine=cosine,
        anchor_directional=da,
        paired_directional=dp,
        anchor_zero=za,
        paired_zero=zp,
        total_zero=zt,
        conflict=bool(cosine < spec["cosine_conflict_threshold"]),
        magnitude_imbalance=bool(
            ratio >= spec["norm_ratio_threshold"]
            or ratio <= 1 / spec["norm_ratio_threshold"]
        ),
        anchor_increases=bool(da > spec["directional_increase_threshold"]),
        paired_increases=bool(dp > spec["directional_increase_threshold"]),
    )


def tables(prefixes, parameter_layout, spec):
    rows, checks = [], []
    for h, data in prefixes.items():
        for key, losses, gradients, finite in zip(
            data["keys"], data["losses"], data["gradients"], data["finite"]
        ):
            strategy, seed, epoch = map(int, key)
            identity = dict(
                fit_days=h,
                strategy=spec["strategies"][strategy],
                seed=seed,
                epoch=epoch,
            )
            for group in spec["parameter_groups"]:
                ids = np.concatenate(
                    [
                        np.arange(v["start"], v["stop"])
                        for v in parameter_layout
                        if group == "all" or v["name"].startswith(group + ".")
                    ]
                )
                rows.append(
                    dict(
                        **identity,
                        parameter_group=group,
                        parameters=len(ids),
                        anchor_loss=losses[0],
                        paired_loss=losses[1],
                        total_loss=losses.mean(),
                        **geometry(gradients[0, ids], gradients[1, ids], spec),
                    )
                )
            if epoch == 100:
                direction = descent_direction(gradients, spec["zero_norm_tolerance"])
                for i, epsilon in enumerate(spec["finite_steps"]):
                    for block, name in enumerate(("anchor", "paired")):
                        actual = (finite[i, 1, block] - finite[i, 0, block]) / (
                            2 * epsilon
                        )
                        expected = (
                            np.nan
                            if direction is None
                            else float(gradients[block] @ direction)
                        )
                        checks.append(
                            dict(
                                **identity,
                                block=name,
                                epsilon=epsilon,
                                analytic=expected,
                                central_difference=actual,
                                absolute_error=abs(actual - expected),
                                skipped=direction is None,
                                passed=bool(
                                    direction is not None
                                    and np.isclose(
                                        actual,
                                        expected,
                                        atol=spec["finite_atol"],
                                        rtol=spec["finite_rtol"],
                                    )
                                ),
                            )
                        )
    return pd.DataFrame(rows), pd.DataFrame(checks)
