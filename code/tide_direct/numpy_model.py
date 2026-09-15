"""Independent dense-model forward from persisted state arrays."""

import numpy as np


def forward(state, history, covariates):
    s = {
        k: v.detach().cpu().numpy() if hasattr(v, "detach") else np.asarray(v)
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
    identity = s["point.weight"][np.tile(np.arange(4), count)]
    z = np.concatenate([y, features.reshape(total, -1), identity], axis=1)
    decoded = block(block(z, "encoder"), "decoder").reshape(total, slots, 2)
    per_time = np.concatenate([decoded, features[:, lookback:]], axis=2)
    raw = block(per_time, "temporal")[:, :, 0] + affine(y, "linear")
    q = raw - raw[:, 0:1]
    return q.reshape(count, 4, slots).transpose(0, 2, 1)
