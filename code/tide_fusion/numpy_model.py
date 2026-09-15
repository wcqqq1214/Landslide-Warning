"""Independent NumPy replay of both additive arms and the fixed bound."""

import numpy as np
from tide_direct.numpy_model import forward as original_forward


def parts(state, history, covariates, arm):
    s = {
        k: v.detach().numpy() if hasattr(v, "detach") else np.asarray(v)
        for k, v in state.items()
    }
    if not any(k.startswith("main.") for k in s):
        return (original_forward(s, history, covariates),)
    x = covariates.copy()
    x[..., 7:10] = 0
    main = original_forward(
        {k[5:]: v for k, v in s.items() if k.startswith("main.")}, history, x
    )

    def affine(x, key):
        return (
            np.einsum("...i,oi->...o", x, s[key + ".weight"], optimize=False)
            + s[key + ".bias"]
        )

    def dense(x, key):
        return affine(np.maximum(affine(x, key + ".a"), 0), key + ".b") + affine(
            x, key + ".skip"
        )

    count, points, lookback = history.shape
    slots = covariates.shape[2] - lookback
    hx = covariates[..., [7, 8, 9, 10, 11]].reshape(count * points, lookback + slots, 5)
    feature = dense(hx, "h_feature").reshape(count * points, -1)
    identity = s["h_point.weight"][np.tile(np.arange(4), count)]
    raw = dense(
        dense(np.concatenate([feature, identity], axis=-1), "h_encoder"), "h_decoder"
    )
    raw = (raw - raw[:, :1]).reshape(count, 4, slots).transpose(0, 2, 1)
    cap = s["cap_norm"][None, None, :]
    actual = cap * np.tanh(raw / cap) if arm == "TiDE_BOUND" else raw
    return main, raw, actual


def forward(state, history, covariates, arm):
    values = parts(state, history, covariates, arm)
    return values[0] if len(values) == 1 else values[0] + values[2]
