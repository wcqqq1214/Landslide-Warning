"""Independent NumPy daily Mamba recurrence and explicit causal attention.

Does not call the training models, PyTorch attention, or parallel scan.
"""

import math

import numpy as np
from scipy.special import erf, expit


def linear(x, weight, bias=None):
    out = np.einsum("ti,oi->to", x, weight, optimize=False)
    return out if bias is None else out + bias


def norm(x, weight, bias):
    centered = x - x.mean(-1, keepdims=True)
    return (
        centered / np.sqrt((centered**2).mean(-1, keepdims=True) + 1e-5) * weight + bias
    )


def conv(x, weight, bias, depthwise=False):
    out = np.broadcast_to(bias, (len(x), len(bias))).copy()
    kernel = weight.shape[-1]
    for k in range(kernel):
        shift = kernel - 1 - k
        if shift < len(x):
            if depthwise:
                out[shift:] += x[: len(x) - shift] * weight[:, 0, k]
            else:
                out[shift:] += linear(x[: len(x) - shift], weight[:, :, k])
    return out


def numpy_output(state, z, family):
    """Return normalized four-point output, from batch-free time×channel input."""
    if family == "TRANSFORMER":
        z = linear(z, state["input.weight"], state["input.bias"])
        angles = np.arange(len(z))[:, None] * np.exp(
            np.arange(0, 16, 2) * (-math.log(10000) / 16)
        )
        pos = np.stack((np.sin(angles), np.cos(angles)), axis=-1).reshape(len(z), 16)
        z += pos
        forbidden = np.triu(np.ones((len(z), len(z)), bool), 1)
        for i in range(2):
            p = f"blocks.{i}."
            v = norm(z, state[p + "norm1.weight"], state[p + "norm1.bias"])
            q, k, v = np.split(
                linear(
                    v,
                    state[p + "attention.in_proj_weight"],
                    state[p + "attention.in_proj_bias"],
                ),
                3,
                axis=1,
            )
            q, k, v = [a.reshape(len(z), 2, 8).transpose(1, 0, 2) for a in (q, k, v)]
            logits = np.einsum("hid,hjd->hij", q, k, optimize=False) / math.sqrt(8)
            logits[:, forbidden] = -np.inf
            prob = np.exp(logits - logits.max(-1, keepdims=True))
            prob /= prob.sum(-1, keepdims=True)
            attended = np.einsum("hij,hjd->hid", prob, v, optimize=False)
            attended = attended.transpose(1, 0, 2).reshape(len(z), 16)
            z += linear(
                attended,
                state[p + "attention.out_proj.weight"],
                state[p + "attention.out_proj.bias"],
            )
            v = norm(z, state[p + "norm2.weight"], state[p + "norm2.bias"])
            v = linear(v, state[p + "fc1.weight"], state[p + "fc1.bias"])
            v = 0.5 * v * (1 + erf(v / math.sqrt(2)))
            z += linear(v, state[p + "fc2.weight"], state[p + "fc2.bias"])
    elif family == "CNN_MAMBA":
        z = conv(z, state["input.weight"], state["input.bias"])
        z = z * expit(z)
        for i in range(2):
            p = f"blocks.{i}."
            m = p + "mixer."
            x = norm(z, state[p + "norm.weight"], state[p + "norm.bias"])
            u, gate = np.split(linear(x, state[m + "in_proj.weight"]), 2, axis=1)
            u = conv(
                u, state[m + "conv.weight"], state[m + "conv.bias"], depthwise=True
            )
            u = u * expit(u)
            proj = linear(u, state[m + "x_proj.weight"])
            dt = linear(
                proj[:, :1], state[m + "dt_proj.weight"], state[m + "dt_proj.bias"]
            )
            dt = np.logaddexp(0, dt)
            B, C = proj[:, 1:17], proj[:, 17:]
            A = -np.exp(state[m + "A_log"])
            h = np.zeros_like(A)
            ys = []
            for t in range(len(z)):
                h = (
                    np.exp(dt[t, :, None] * A) * h
                    + dt[t, :, None] * B[t, None, :] * u[t, :, None]
                )
                yt = (h * C[t, None, :]).sum(-1) + state[m + "D"] * u[t]
                ys.append(yt * gate[t] * expit(gate[t]))
            z += linear(np.array(ys), state[m + "out_proj.weight"])
    else:
        raise ValueError("Unknown architecture")
    z = norm(z, state["norm.weight"], state["norm.bias"])
    return linear(z, state["head.weight"], state["head.bias"])


def numpy_forward(checkpoint, x, physical):
    state = {k: v.detach().numpy() for k, v in checkpoint["state_dict"].items()}
    s = checkpoint["scaling"]
    z = (x - np.array(s["x_mean"])) / np.array(s["x_std"])
    family = (
        "TRANSFORMER" if checkpoint["arm"].startswith("TRANSFORMER") else "CNN_MAMBA"
    )
    base = (
        physical
        if "_BRES_" in checkpoint["arm"]
        else np.broadcast_to(s["y0"], physical.shape)
    )
    return base + numpy_output(state, z, family) * np.array(s["unit"])
