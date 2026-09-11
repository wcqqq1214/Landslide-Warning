"""NumPy recurrence with an explicit encoder/decoder state reset control."""

import numpy as np

from physics_guided_sequence.reference import sigmoid


def predict(state_dict, encoder, decoder, variant, budget=None):
    if variant not in ("CARRY", "RESET"):
        raise ValueError("Unknown reference variant")
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
        if step == 30 and variant == "RESET":
            hidden.fill(0)
            cell.fill(0)
        extended = np.pad(
            np.concatenate([value, hidden], axis=1), ((0, 0), (0, 0), (1, 1))
        )
        gates = np.empty((len(value), 64, 4))
        for point in range(4):
            gates[:, :, point] = (
                np.einsum("bck,ock->bo", extended[:, :, point : point + 3], gate)
                + weights["gates.bias"]
            )
        ingate, forget, outgate, candidate = np.split(gates, 4, axis=1)
        cell = sigmoid(forget) * cell + sigmoid(ingate) * np.tanh(candidate)
        hidden = sigmoid(outgate) * np.tanh(cell)
        if step >= 30:
            result.append(
                100
                * (
                    np.einsum("bcp,c->bp", hidden, weights["output.weight"].reshape(16))
                    + weights["output.bias"][0]
                )
            )
    return np.stack(result, axis=1)
