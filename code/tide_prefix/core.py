"""Exact prefix queries of the original TiDE, with unchanged parameters."""

import numpy as np
import torch
from torch.nn import functional as F
from tide_features import core as prior

o, ROOT = prior.o, prior.ROOT
CONFIG = ROOT / "config/ootang_tide_prefix.v1_0.json"
SOURCES = ROOT / "docs/ootang_tide_prefix_sources.v1.0.json"
B, BA = prior.B, prior.BA
scaling, schedule, loss = prior.scaling, prior.schedule, prior.loss


def spec():
    return o.read_json(CONFIG)


def guard(implementation=True):
    files = o.read_json(SOURCES)["files"]
    for p, digest in files.items():
        assert o.sha(ROOT / p) == digest, p
    if implementation:
        o.verify_lock(ROOT / spec()["out"] / "implementation_lock.json")
    return len(files)


def arrays(cfg, teachers, y, forcing, origins, sc, arm, current=False, targets=False):
    if arm not in cfg["arms"]:
        raise ValueError(arm)
    return prior.arrays(
        prior.spec(), teachers, y, forcing, origins, sc, "TiDE_KIN", current, targets
    )


def mask_after(cov, length, h):
    result = cov.clone()
    result[..., length + h + 1 :, :10] = 0
    result[..., length + h + 1 :, 11] = 0
    return result


class Tide(prior.Tide):
    """q(h) = original Tide(history, mask_after(cov,h))[h]."""

    def forward(self, hist, cov):
        batch = hist.shape[0]
        y = hist.reshape(batch * 4, self.length)
        x = cov.reshape(batch * 4, self.length + self.slots, 12)
        actual = self.feature(x)
        future = x[:, self.length :]
        masked = torch.zeros_like(future)
        masked[..., 10] = future[..., 10]
        fm = self.feature(masked)
        df = actual[:, self.length :] - fm
        point = self.point(torch.arange(4).repeat(batch))
        base = torch.cat(
            [y, actual[:, : self.length].flatten(1), fm.flatten(1), point], dim=-1
        )
        lo = self.length + self.length * fm.shape[-1]
        hi = lo + self.slots * fm.shape[-1]

        def prefix_affine(layer):
            weight = layer.weight[:, lo:hi].reshape(-1, self.slots, fm.shape[-1])
            contributions = torch.einsum("bsf,ksf->bsk", df, weight)
            return layer(base)[:, None] + contributions.cumsum(1)

        encoded = self.encoder.b(
            torch.relu(prefix_affine(self.encoder.a))
        ) + prefix_affine(self.encoder.skip)
        hidden = torch.relu(self.decoder.a(encoded))
        width = self.decoder.b.out_features // self.slots

        def selected(layer, inputs):
            w = layer.weight.reshape(self.slots, width, -1)
            b = layer.bias.reshape(self.slots, width)
            return torch.einsum("bsi,soi->bso", inputs, w) + b

        decoded_h = selected(self.decoder.b, hidden) + selected(
            self.decoder.skip, encoded
        )
        decoded_zero = F.linear(
            hidden, self.decoder.b.weight[:width], self.decoder.b.bias[:width]
        )
        decoded_zero = decoded_zero + F.linear(
            encoded, self.decoder.skip.weight[:width], self.decoder.skip.bias[:width]
        )
        local = actual[:, self.length :]
        raw_h = self.temporal(torch.cat([decoded_h, local], dim=-1)).squeeze(-1)
        # The subtractand must use the SAME prefix as its forecast query.
        raw_zero = self.temporal(
            torch.cat([decoded_zero, local[:, :1].expand(-1, self.slots, -1)], dim=-1)
        ).squeeze(-1)
        linear = self.linear(y)
        q = raw_h - raw_zero + linear - linear[:, :1]
        q = torch.cat([torch.zeros_like(q[:, :1]), q[:, 1:]], dim=1)
        return q.reshape(batch, 4, self.slots).transpose(1, 2)


def predict(cfg, model, teachers, y, forcing, n, end, sc, arm):
    if len(y) != n or len(forcing) != end or end - n != 293:
        raise ValueError("Exact issue prefix and full conditional scenario required")
    inputs = arrays(cfg, teachers, y, forcing, [n], sc, arm, current=True)
    model.eval()
    with torch.no_grad():
        q = model(*inputs[:2]).numpy()[0]
    assert np.array_equal(q[0], np.zeros(4))
    change = q[1:] * np.asarray(sc["unit"])
    mu = y[-1] + change
    if not np.isfinite(mu).all():
        raise ArithmeticError("Nonfinite forecast")
    return mu, change


def checkpoint_folder(cfg, n, arm, seed):
    parent = cfg["prior_tide_out"] if arm == "TiDE_KIN" else cfg["out"]
    return ROOT / parent / f"origin_{n}/{arm}/seed_{seed}"


def reload(path):
    ck = torch.load(path, map_location="cpu", weights_only=True)
    model = (
        prior.Tide(spec(), ck["seed"])
        if ck["arm"] == "TiDE_KIN"
        else Tide(spec(), ck["seed"])
    )
    model.load_state_dict(ck["state_dict"], strict=True)
    return model, ck["scaling"], ck


def literal_queries(model, hist, cov, horizons=None):
    """Unoptimized original-network oracle; only used for verification."""
    horizons = list(range(model.slots)) if horizons is None else list(horizons)
    # A query is an original forward with its own h-limited covariates.
    masked = torch.cat([mask_after(cov, model.length, h) for h in horizons], dim=0)
    history = torch.cat([hist for _ in horizons], dim=0)
    out = prior.Tide.forward(model, history, masked)
    out = out.reshape(len(horizons), hist.shape[0], model.slots, 4)
    return torch.stack([out[i, :, h] for i, h in enumerate(horizons)], dim=1)
