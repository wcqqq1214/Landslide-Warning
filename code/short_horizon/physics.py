"""Exact original B+ forcing replay and differentiable daily state constraints."""

import copy

import numpy as np
import torch

from physics_guided.reference import load
from physics_guided.mechanics import library
from rolling_probability.data import SOURCES
from .common import ROOT, CALLS


def pack_state(states):
    return np.ascontiguousarray(
        np.concatenate(
            [
                states["coordinates"] - states["background"],
                states["plastic"],
                states["basal_reaction"],
                states["contact"],
                states["bulk_reaction"],
                states["background"],
            ],
            axis=-1,
        )
    )


def coefficients(theta, ctx, state):
    dt = 1 / 64
    beta = dt / (np.exp(theta[20:24]) + dt)
    ar = 1 / (1 + dt / np.exp(theta[16:20]))
    kr = np.exp(theta[12:16]) * ctx.length
    ac = 1 / (1 + dt / np.exp(theta[42]))
    ae = 1 / (1 + dt / np.exp(theta[43]))
    kc, ke = state["kc"], state["ke"]
    A = (
        ac * kc
        + ae * ke
        + np.diag((np.exp(theta[8:12]) * ctx.length / dt + ar * kr) / beta)
    )
    maps = np.zeros((16, 4, 4))
    for mask in range(16):
        ids = [i for i in range(4) if not mask & (1 << i)]
        if ids:
            maps[mask][np.ix_(ids, ids)] = np.linalg.inv(A[np.ix_(ids, ids)])
    return np.ascontiguousarray(
        np.r_[beta, ar, kr, ac, ae, kc.ravel(), ke.ravel(), A.ravel(), maps.ravel()]
    )


class PhysicalBank:
    def __init__(self, forcing, y0, prefixes):
        self.ref = load(ROOT / "runtime/ootang_rolling_v3/reference")
        self.forcing, self.y0 = np.asarray(forcing, float), np.asarray(y0, float)
        self.geometry = self.ref.Context(self.forcing)
        self.obs = self.geometry.obs.copy()
        self.theta, self.actual, self.coeff = {}, {}, {}
        for q in prefixes:
            if q in SOURCES:
                with np.load(SOURCES[q]) as a:
                    theta = a["theta"].copy()
            else:
                import json

                cfg = json.loads(
                    (self.ref.ROOT / "results/calibrated.json").read_text()
                )
                theta = np.asarray(cfg["theta"], float)
            mean, states = self.forward(theta, self.forcing)
            self.theta[q] = theta
            self.actual[q] = (mean + self.y0, states, pack_state(states))
            self.coeff[q] = coefficients(theta, self.geometry, states)

    def context(self, forcing):
        # Geometry is independent of the date sequence. Hydraulic arrays are not.
        ctx = copy.copy(self.geometry)
        ctx.P, ctx.R = np.asarray(forcing, float).T
        ctx.support = ctx.lookup(ctx.R, ctx.ext) - ctx.lookup([175], ctx.ext)
        return ctx

    def forward(self, theta, forcing):
        CALLS["reference_forward"] += 1
        return self.ref.forward(theta, self.context(forcing), states=True)

    def forecast(self, n, horizon=7):
        q = max(q for q in self.theta if q <= n)
        future = np.tile(
            [self.forcing[n - 7 : n, 0].mean(), self.forcing[n - 1, 1]], (horizon, 1)
        )
        forcing = np.concatenate([self.forcing[:n], future])
        mean, states = self.forward(self.theta[q], forcing)
        packed = pack_state(states)
        error = float(np.max(abs(packed[:n] - self.actual[q][2][:n])))
        if error > 1e-8:
            raise ArithmeticError("Future forcing changed historical physical state")
        return q, forcing, mean + self.y0, states, packed, error


_LIB = None


def day_numpy(old, end, force, elastic, coeff, previous=0):
    global _LIB
    if _LIB is None:
        _LIB = library()
    result, masks, audit = np.empty(24), np.empty(64, np.int32), np.empty(5)
    CALLS["day_forward"] += 1
    bad = _LIB.day_forward(
        np.ascontiguousarray(coeff),
        np.ascontiguousarray(old),
        np.ascontiguousarray(end),
        np.ascontiguousarray(force),
        np.ascontiguousarray(elastic),
        int(previous),
        result,
        masks,
        audit,
    )
    if bad or not np.isfinite(result).all():
        raise ArithmeticError("Daily active-set solve failed")
    return result, masks, audit


class BatchDay(torch.autograd.Function):
    @staticmethod
    def forward(ctx, old, end, force, elastic, coeff):
        shape = old.shape
        s = np.ascontiguousarray(old.detach().cpu().numpy().reshape(-1, 24))
        b, f, e = [
            np.ascontiguousarray(v.detach().cpu().numpy().reshape(-1, 4))
            for v in (end, force, elastic)
        ]
        c = np.ascontiguousarray(coeff.detach().cpu().numpy().reshape(len(s), -1))
        out = np.empty_like(s)
        masks = np.empty((len(s), 64), np.int32)
        for i in range(len(s)):
            out[i], masks[i], _ = day_numpy(s[i], b[i], f[i], e[i], c[i])
        ctx.coeff, ctx.masks, ctx.shape = c, masks, shape
        return torch.from_numpy(out.reshape(shape))

    @staticmethod
    def backward(ctx, grad):
        g = np.ascontiguousarray(grad.detach().cpu().numpy().reshape(-1, 24))
        result = np.empty_like(g)
        unused_end = np.empty(4)
        for i in range(len(g)):
            CALLS["day_backward"] += 1
            _LIB.day_backward(ctx.coeff[i], ctx.masks[i], g[i], result[i], unused_end)
        return torch.from_numpy(result.reshape(ctx.shape)), None, None, None, None


def replay(initial, background, force, elastic, coeff):
    out, old, previous, audits = [], initial.copy(), 0, []
    for h in range(len(background)):
        old, masks, audit = day_numpy(
            old, background[h], force[h], elastic[h], coeff, previous
        )
        out.append(old.copy())
        audits.append(audit)
        previous = int(masks[-1])
    return np.asarray(out), np.asarray(audits)
