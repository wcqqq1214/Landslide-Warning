"""Independent calendar enumeration and NumPy convolution/recurrent reference."""

import numpy as np


def inputs(base, features, labels, origin, horizon, physical, history):
    past = np.empty((1, 30, 23, 1, 4))
    future = np.zeros((1, horizon, 23, 1, 4))
    for i, day in enumerate(range(origin - 30, origin)):
        past[0, i, :20, 0] = (features[day] - physical.mean) / physical.scale
        e = labels[day] - base[day]
        previous = labels[day - 1] - base[day - 1]
        past[0, i, 20, 0] = (e - history.mean[0]) / history.scale[0]
        past[0, i, 21, 0] = (e - previous - history.mean[1]) / history.scale[1]
        past[0, i, 22, 0] = (day - origin) / 179
    for lead, day in enumerate(range(origin, origin + horizon)):
        future[0, lead, :20, 0] = (features[day] - physical.mean) / physical.scale
        future[0, lead, 22, 0] = lead / 179
    return past, future, base[origin : origin + horizon][None].copy()


def sigmoid(x):
    # Stable for the saturated old-teacher / current-scaler probe cases as well.
    result = np.empty_like(x)
    positive = x >= 0
    result[positive] = 1 / (1 + np.exp(-x[positive]))
    exp = np.exp(x[~positive])
    result[~positive] = exp / (1 + exp)
    return result


def predict(state_dict, encoder, decoder, budget=None):
    weights = {name: value.detach().numpy() for name, value in state_dict.items()}
    gate = weights["gates.weight"][:, :, 0, :]
    hidden = np.zeros((encoder.shape[0], 16, 4))
    cell = np.zeros_like(hidden)
    result = []
    for step, value in enumerate(
        np.concatenate([encoder, decoder], axis=1)[:, :, :, 0].transpose(1, 0, 2, 3)
    ):
        if budget is not None:
            budget.tick("reference_sample_steps", len(value))
        extended = np.pad(
            np.concatenate([value, hidden], axis=1), ((0, 0), (0, 0), (1, 1))
        )
        gates = np.empty((len(value), 64, 4))
        for point in range(4):
            gates[:, :, point] = (
                np.einsum("bck,ock->bo", extended[:, :, point : point + 3], gate)
                + weights["gates.bias"]
            )
        input_gate, forget, output_gate, candidate = np.split(gates, 4, axis=1)
        cell = sigmoid(forget) * cell + sigmoid(input_gate) * np.tanh(candidate)
        hidden = sigmoid(output_gate) * np.tanh(cell)
        if step >= 30:
            result.append(
                100
                * (
                    np.einsum("bcp,c->bp", hidden, weights["output.weight"].reshape(16))
                    + weights["output.bias"][0]
                )
            )
    return np.stack(result, axis=1)
