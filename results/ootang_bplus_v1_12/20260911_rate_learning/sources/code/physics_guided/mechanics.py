"""Full-history autograd: save daily active branches, reverse every 64-substep day."""

import ctypes
import subprocess
import sys
from pathlib import Path
import numpy as np
import torch

from .reference import ROOT, sha


def tensor(a):
    return torch.as_tensor(a, dtype=torch.float64, device="cpu")


def library():
    source = Path(__file__).with_suffix(".c")
    path = (
        ROOT
        / "runtime/ootang_bplus_v1_1"
        / (
            "mechanics_"
            + sha(source)[:16]
            + (".dylib" if sys.platform == "darwin" else ".so")
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        subprocess.run(
            ["cc", "-O3", "-fPIC", "-shared", str(source), "-o", str(path)],
            check=True,
            capture_output=True,
        )
    lib = ctypes.CDLL(str(path))
    ptr = np.ctypeslib.ndpointer(dtype=np.float64, flags="C_CONTIGUOUS")
    iptr = np.ctypeslib.ndpointer(dtype=np.int32, flags="C_CONTIGUOUS")
    lib.day_forward.argtypes = [ptr, ptr, ptr, ptr, ptr, ctypes.c_int, ptr, iptr, ptr]
    lib.day_forward.restype = ctypes.c_int
    lib.day_backward.argtypes = [ptr, iptr, ptr, ptr, ptr]
    lib.day_backward.restype = None
    return lib


class Day(torch.autograd.Function):
    @staticmethod
    def forward(ctx, old, end, solver, t, previous):
        out, masks, audit = solver.numpy_day(
            old.detach().numpy(), end.detach().numpy(), t, previous
        )
        ctx.solver, ctx.masks = solver, masks
        branch, checks = torch.tensor(masks[-1], dtype=torch.int32), tensor(audit)
        ctx.mark_non_differentiable(branch, checks)
        return tensor(out), branch, checks

    @staticmethod
    def backward(ctx, grad, _mask, _audit):
        old, end = np.empty(24), np.empty(4)
        ctx.solver.lib.day_backward(
            ctx.solver.coeff, ctx.masks, np.ascontiguousarray(grad.numpy()), old, end
        )
        return tensor(old), tensor(end), None, None, None


class Mechanics:
    def __init__(self, ref, theta, drivers):
        self.theta = np.asarray(theta, dtype=np.float64)
        self.ctx = ref.Context(drivers.forcing)
        self.u, self.reference = ref.forward(theta, self.ctx, states=True)
        th, s = self.theta, self.reference
        dt = 1 / 64
        beta = dt / (np.exp(th[20:24]) + dt)
        ar = 1 / (1 + dt / np.exp(th[16:20]))
        kr = np.exp(th[12:16]) * self.ctx.length
        ac, ae = 1 / (1 + dt / np.exp(th[42])), 1 / (1 + dt / np.exp(th[43]))
        kc, ke = s["kc"], s["ke"]
        a = (
            ac * kc
            + ae * ke
            + np.diag((np.exp(th[8:12]) * self.ctx.length / dt + ar * kr) / beta)
        )
        maps = np.zeros((16, 4, 4))
        for mask in range(16):
            ids = [i for i in range(4) if not mask & (1 << i)]
            if ids:
                maps[mask][np.ix_(ids, ids)] = np.linalg.inv(a[np.ix_(ids, ids)])
        self.coeff = np.ascontiguousarray(
            np.r_[beta, ar, kr, ac, ae, kc.ravel(), ke.ravel(), a.ravel(), maps.ravel()]
        )
        self.elastic = np.ascontiguousarray(s["rain_head"] * th[[44, 45, 46, 46]])
        self.force = np.ascontiguousarray(s["force"])
        self.lib = library()

    def numpy_day(self, old, end, t, previous):
        out, masks, audit = np.empty(24), np.empty(64, dtype=np.int32), np.empty(5)
        bad = self.lib.day_forward(
            self.coeff,
            np.ascontiguousarray(old),
            np.ascontiguousarray(end),
            self.force[t],
            self.elastic[t],
            int(previous),
            out,
            masks,
            audit,
        )
        if bad or not np.isfinite(out).all():
            raise ArithmeticError(f"Invalid B+ mechanical day t={t}")
        if audit[0] < -1e-8 or audit[1] < -1e-7 or audit[2] > 1e-7 or audit[3] < 0:
            raise ArithmeticError(f"Mechanical constraints failed t={t}: {audit}")
        return out, masks, audit

    def zero_trajectory(self):
        old = np.zeros(24)
        states, masks, checks = [old.copy()], [], []
        previous = 0
        for t in range(1, len(self.u)):
            old, branch, audit = self.numpy_day(
                old, self.reference["background"][t], t, previous
            )
            previous = branch[-1]
            states.append(old.copy())
            masks.append(branch)
            checks.append(audit)
        return np.array(states), np.array(masks), np.array(checks)
