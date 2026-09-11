"""Pack existing queries without adding supervision; share the fixed optimizer rule."""

from dataclasses import dataclass
import math

import numpy as np
import torch

from physics_guided.training import setup, update
from physics_guided_balanced_origin.core import profile
from physics_guided_sequence.core import SequenceM1, make_sequence


@dataclass
class Batch:
    encoder: torch.Tensor
    decoder: torch.Tensor
    baseline: torch.Tensor
    row_batch: torch.Tensor
    row_lead: torch.Tensor
    weights: torch.Tensor
    blocks: np.ndarray
    targets: torch.Tensor | None
    groups: list
    lengths: list

    def payload(self):
        return {
            name: value.detach().numpy()
            if isinstance(value, torch.Tensor)
            else np.asarray(value)
            for name, value in vars(self).items()
            if value is not None
        }


def make_batch(table, pool, labels, scalers, teacher_column, supervised):
    for key in ("origin", "target", teacher_column):
        if table[key].dtype.kind not in "iu":
            raise ValueError("Integer origin, target and teacher identities required")
    if (
        len(table) == 0
        or labels.ndim != 2
        or labels.shape[1:] != (4,)
        or not np.isfinite(labels).all()
    ):
        raise ValueError("Nonempty queries and four-point observation prefix required")
    if supervised and (table.target.min() < 0 or table.target.max() >= len(labels)):
        raise ValueError("Supervision must end within the observation prefix")
    rows = table.to_dict("records")
    groups, lengths = [], []
    mapping = {}
    for row in rows:
        key = (int(row[teacher_column]), int(row["origin"]))
        horizon = int(row["target"]) - key[1] + 1
        if not 1 <= horizon <= 180 or not 31 <= key[1] <= len(labels):
            raise ValueError("Query exceeds available history or horizon")
        if key not in mapping:
            mapping[key] = len(groups)
            groups.append(key)
            lengths.append(horizon)
        else:
            lengths[mapping[key]] = max(lengths[mapping[key]], horizon)
    sequences = [
        make_sequence(*pool[teacher], labels[:origin], origin, length, *scalers)
        for (teacher, origin), length in zip(groups, lengths)
    ]
    horizon = max(lengths)
    decoder = torch.zeros((len(groups), horizon, 23, 1, 4), dtype=torch.float64)
    base = torch.zeros((len(groups), horizon, 4), dtype=torch.float64)
    for i, sequence in enumerate(sequences):
        decoder[i, : lengths[i]] = sequence.decoder[0]
        base[i, : lengths[i]] = sequence.baseline[0]
    weights = table.weight.to_numpy() if supervised else np.zeros(len(table))
    blocks = table.block.to_numpy() if supervised else np.full(len(table), -1)
    if supervised and (
        not np.isfinite(weights).all()
        or (weights < 0).any()
        or set(blocks) != {0, 1}
        or any(
            not np.isclose(weights[blocks == b].sum(), 0.5, rtol=0, atol=1e-14)
            for b in (0, 1)
        )
    ):
        raise ValueError("The original two half-weight blocks are required")
    return Batch(
        torch.cat([s.encoder for s in sequences]),
        decoder,
        base,
        torch.tensor(
            [mapping[(int(r[teacher_column]), int(r["origin"]))] for r in rows]
        ),
        torch.tensor((table.target - table.origin).to_numpy()),
        torch.as_tensor(weights, dtype=torch.float64),
        blocks,
        torch.as_tensor(labels[table.target.to_numpy()], dtype=torch.float64)
        if supervised
        else None,
        groups,
        lengths,
    )


def new_model(seed):
    setup(seed)
    return SequenceM1()


def output(model, encoder, decoder, variant, budget=None):
    if variant not in ("CARRY", "RESET"):
        raise ValueError("Unknown state transfer control")
    if budget:
        budget.tick("model_calls")
    state = model.encode(encoder, budget)
    if variant == "RESET":
        state = tuple(torch.zeros_like(value) for value in state)
    return model.decode(decoder, state, budget)[0]


def block_losses(batch, correction):
    if batch.targets is None:
        raise ValueError("Prediction inputs contain no supervised targets")
    selected = (batch.row_batch, batch.row_lead)
    error = (
        ((batch.baseline[selected] + correction[selected] - batch.targets) / 100)
        .square()
        .mean(dim=1)
    )
    return torch.stack(
        [
            2 * (error[batch.blocks == b] * batch.weights[batch.blocks == b]).sum()
            for b in (0, 1)
        ]
    )


def loss_values(model, batch, variant, budget=None):
    with torch.no_grad():
        return block_losses(
            batch, output(model, batch.encoder, batch.decoder, variant, budget)
        ).numpy()


def mean_step(model, op, batch, variant, budget):
    op.zero_grad(set_to_none=True)
    losses = block_losses(
        batch, output(model, batch.encoder, batch.decoder, variant, budget)
    )
    parameters = tuple(model.parameters())
    gradients = []
    for b in (0, 1):
        budget.tick("gradient_calls")
        values = torch.autograd.grad(losses[b], parameters, retain_graph=b == 0)
        gradients.append(torch.cat([v.reshape(-1) for v in values]).numpy())
    record, mixed = profile(losses.detach().numpy(), np.stack(gradients))
    start = 0
    for parameter in parameters:
        end = start + parameter.numel()
        parameter.grad = torch.tensor(mixed[start:end].reshape(parameter.shape))
        start = end
    budget.tick("neural_updates")
    clipped_from = update(model, op)
    if not math.isclose(
        clipped_from, record["gradient_norm"], rel_tol=1e-9, abs_tol=1e-10
    ):
        raise ArithmeticError(
            "Clipping norm differs from the registered mixed gradient"
        )
    return record


def predict(model, batch, variant, base, targets, budget=None):
    with torch.no_grad():
        corrections = output(model, batch.encoder, batch.decoder, variant, budget)
    mean = np.full_like(base, np.nan)
    mean[29:31] = base[29:31]
    mean[targets] = base[targets] + corrections[batch.row_batch, batch.row_lead].numpy()
    if not np.isfinite(mean[29:]).all():
        raise ValueError("Incomplete finite mean forecast")
    return mean


def history_diagnostics(model, batch, variant, h, strategy, seed, points, budget):
    group = batch.groups.index((h, h))
    encoder = batch.encoder[group : group + 1].clone().requires_grad_(True)
    decoder = batch.decoder[group : group + 1]
    values = output(model, encoder, decoder, variant, budget)
    records = []
    for lead in (0, 29, 179):
        for point, station in enumerate(points):
            budget.tick("gradient_calls")
            (gradient,) = torch.autograd.grad(
                values[0, lead, point], encoder, retain_graph=True, allow_unused=True
            )
            if gradient is None:
                norm = 0.0
            else:
                norm = math.hypot(
                    *gradient[:, :, 20:22].detach().numpy().reshape(-1).tolist()
                )
            if not math.isfinite(norm) or (variant == "RESET" and norm != 0):
                raise ValueError("Invalid or unexpected reset-history gradient")
            records.append(
                dict(
                    fit_days=h,
                    strategy=strategy,
                    seed=seed,
                    station=station,
                    lead=lead,
                    history_gradient_norm=norm,
                )
            )
    return records
