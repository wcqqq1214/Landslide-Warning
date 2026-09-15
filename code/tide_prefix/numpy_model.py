"""Independent NumPy prefix-query network from saved state arrays."""

import numpy as np


def forward(state, history, covariates):
    s = {
        k: v.detach().numpy() if hasattr(v, "detach") else np.asarray(v)
        for k, v in state.items()
    }

    def affine(x, name):
        return (
            np.einsum("...i,oi->...o", x, s[name + ".weight"], optimize=False)
            + s[name + ".bias"]
        )

    def block(x, name):
        return affine(np.maximum(affine(x, name + ".a"), 0), name + ".b") + affine(
            x, name + ".skip"
        )

    count, points, lookback = history.shape
    total = count * points
    y = history.reshape(total, lookback)
    cov = covariates.reshape(total, covariates.shape[2], 12)
    slots = cov.shape[1] - lookback
    features = block(cov, "feature")
    absent = np.zeros_like(cov[:, lookback:])
    absent[..., 10] = cov[:, lookback:, 10]
    masked = block(absent, "feature")
    delta = features[:, lookback:] - masked
    identity = s["point.weight"][np.tile(np.arange(4), count)]
    base = np.concatenate(
        [
            y,
            features[:, :lookback].reshape(total, -1),
            masked.reshape(total, -1),
            identity,
        ],
        axis=1,
    )
    first = lookback + lookback * 2

    def prefix(layer):
        w = s[layer + ".weight"][:, first : first + slots * 2].reshape(-1, slots, 2)
        increment = np.einsum("bsi,ksi->bsk", delta, w, optimize=False)
        return affine(base, layer)[:, None, :] + np.cumsum(increment, axis=1)

    encoded = affine(np.maximum(prefix("encoder.a"), 0), "encoder.b") + prefix(
        "encoder.skip"
    )
    hidden = np.maximum(affine(encoded, "decoder.a"), 0)

    def select(x, name, anchor=False):
        w, b = (
            s[name + ".weight"].reshape(slots, 2, -1),
            s[name + ".bias"].reshape(slots, 2),
        )
        if anchor:
            return np.einsum("bsi,oi->bso", x, w[0], optimize=False) + b[0]
        return np.einsum("bsi,soi->bso", x, w, optimize=False) + b

    decoded = select(hidden, "decoder.b") + select(encoded, "decoder.skip")
    zero = select(hidden, "decoder.b", True) + select(encoded, "decoder.skip", True)
    local = features[:, lookback:]
    head = block(np.concatenate([decoded, local], axis=-1), "temporal")[..., 0]
    origin = block(
        np.concatenate([zero, np.repeat(local[:, :1], slots, axis=1)], axis=-1),
        "temporal",
    )[..., 0]
    linear = affine(y, "linear")
    q = head - origin + linear - linear[:, :1]
    q[:, 0] = 0
    return q.reshape(count, points, slots).transpose(0, 2, 1)
