"""Independent NumPy implementation of the fixed correction head."""

import numpy as np


def parts(state, x):
    s = {
        k: v.detach().numpy() if hasattr(v, "detach") else np.asarray(v)
        for k, v in state.items()
    }

    def affine(x, name):
        return (
            np.einsum("...i,oi->...o", x, s[name + ".weight"], optimize=False)
            + s[name + ".bias"]
        )

    def dense(x, name):
        return affine(np.maximum(affine(x, name + ".a"), 0), name + ".b") + affine(
            x, name + ".skip"
        )

    batch = len(x)
    f = dense(x.reshape(batch * 4, 474, 5), "h_feature").reshape(batch * 4, -1)
    ids = s["h_point.weight"][np.tile(np.arange(4), batch)]
    q = dense(dense(np.concatenate([f, ids], -1), "h_encoder"), "h_decoder")
    q = (q - q[:, :1]).reshape(batch, 4, 294).transpose(0, 2, 1)
    cap = s["cap_norm"][None, None, :]
    return q, cap * np.tanh(q / cap)
