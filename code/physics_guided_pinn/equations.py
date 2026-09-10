"""Original 64-substep B+ equations expressed as differentiable residuals.

State order is s, p, basal reaction, contact reaction, bulk reaction, background;
each block uses the four physical domains, not the four observation stations.
See docs/ootang_bplus_pinn_equation_contract.v1.8.md for source provenance.
These dimensional residuals do not define loss weights or enforce feasibility.
"""

from dataclasses import dataclass

import torch


@dataclass(frozen=True)
class Coefficients:
    eta: torch.Tensor
    hardening: torch.Tensor
    tau_rest: torch.Tensor
    tau_motion: torch.Tensor
    contact: torch.Tensor
    bulk: torch.Tensor
    tau_contact: torch.Tensor
    tau_bulk: torch.Tensor

    @classmethod
    def from_reference(cls, theta, length, contact, bulk):
        theta = torch.as_tensor(theta, dtype=torch.float64)
        length = torch.as_tensor(length, dtype=theta.dtype, device=theta.device)
        if (
            theta.shape != (54,)
            or length.shape != (4,)
            or not torch.isfinite(theta).all()
            or not torch.isfinite(length).all()
            or (length <= 0).any()
        ):
            raise ValueError("Expected 54 finite reference parameters and four lengths")
        return cls(
            torch.exp(theta[8:12]) * length,
            torch.exp(theta[12:16]) * length,
            torch.exp(theta[16:20]),
            torch.exp(theta[20:24]),
            torch.as_tensor(contact, dtype=theta.dtype, device=theta.device),
            torch.as_tensor(bulk, dtype=theta.dtype, device=theta.device),
            torch.exp(theta[42]),
            torch.exp(theta[43]),
        )

    def validate(self):
        for value in (self.eta, self.hardening, self.tau_rest, self.tau_motion):
            if (
                value.shape != (4,)
                or not torch.isfinite(value).all()
                or (value <= 0).any()
            ):
                raise ValueError("Positive finite four-domain coefficients required")
        for value in (self.contact, self.bulk):
            if value.shape != (4, 4) or not torch.isfinite(value).all():
                raise ValueError("Finite four-domain stiffness matrices required")
        for value in (self.tau_contact, self.tau_bulk):
            if value.ndim != 0 or not torch.isfinite(value) or value <= 0:
                raise ValueError("Positive finite memory time constants required")


def residuals(
    previous, current, force, elastic, background_increment, coefficients, dt=1 / 64
):
    """Return each raw equation and complementarity violation without aggregation.

    The supplied background increment must come from its separately specified
    evolution law. A free neural background cannot silently remove this equation.
    Slip/gap tolerances are applied by the caller, not absorbed into these values.
    """
    if not isinstance(dt, (float, int)) or not 0 < dt < float("inf"):
        raise ValueError("A positive finite substep length is required")
    if previous.shape != current.shape or current.shape[-1:] != (24,):
        raise ValueError("State must contain six four-domain blocks")
    shape = current.shape[:-1] + (4,)
    if any(x.shape != shape for x in (force, elastic, background_increment)):
        raise ValueError("Loads and background must match the state domains")
    if not all(
        torch.isfinite(x).all()
        for x in (previous, current, force, elastic, background_increment)
    ):
        raise ValueError("Nonfinite state or load")
    coefficients.validate()
    c = coefficients
    s0, p0, rb0, rc0, re0, b0 = previous.split(4, dim=-1)
    s, p, rb, rc, re, b = current.split(4, dim=-1)
    beta = dt / (c.tau_motion + dt)
    ar = 1 / (1 + dt / c.tau_rest)
    ac, ae = 1 / (1 + dt / c.tau_contact), 1 / (1 + dt / c.tau_bulk)
    dp = p - p0
    dq = (s + b) - (s0 + b0)
    dx = beta * dp
    gap = c.eta * dp / dt + rb + rc + re - force
    return {
        "motion": s - s0 - beta * (p + elastic - s0),
        "basal_memory": rb - ar * (rb0 + c.hardening * dp),
        "contact_memory": rc - ac * (rc0 + dq @ c.contact.T),
        "bulk_memory": re - ae * (re0 + dq @ c.bulk.T),
        "background": b - b0 - background_increment,
        "slip_step": dx,
        "yield_gap": gap,
        "negative_slip": torch.relu(-dx),
        "negative_gap": torch.relu(-gap),
        "complementarity": dx * gap,
    }


def observe(state, observation_matrix, initial_displacement):
    """Map the physical-domain coordinates to the four measured stations."""
    if (
        state.shape[-1:] != (24,)
        or observation_matrix.shape != (4, 4)
        or initial_displacement.shape != (4,)
    ):
        raise ValueError(
            "Expected physical state, four-point map and common initial displacement"
        )
    if not all(
        torch.isfinite(x).all()
        for x in (state, observation_matrix, initial_displacement)
    ):
        raise ValueError("Nonfinite observation input")
    return (
        state[..., :4] + state[..., 20:24]
    ) @ observation_matrix.T + initial_displacement
