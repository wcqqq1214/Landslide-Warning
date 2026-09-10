"""Parameter-group gradients and an exact, non-predictive motion decomposition."""

import numpy as np
import torch

from physics_guided_state_pinn.core import PHYSICS_KEYS

TERMS = ("data", *PHYSICS_KEYS, "rate_prior")


def norm(vector):
    return float(np.sqrt(np.sum(np.asarray(vector) ** 2)))


def inventory(model):
    offset, rows, groups = 0, [], {"G": [], "H": []}
    for name, parameter in model.named_parameters():
        group = (
            "G"
            if name.startswith("rate_net.")
            else "H"
            if name.startswith("state_net.")
            else None
        )
        if group is None:
            raise ValueError(f"Unknown parameter group: {name}")
        stop = offset + parameter.numel()
        groups[group].extend(range(offset, stop))
        rows.append(
            dict(
                name=name,
                shape=list(parameter.shape),
                start=offset,
                stop=stop,
                group=group,
            )
        )
        offset = stop
    if not all(groups.values()):
        raise ValueError("Both parameter groups required")
    return rows, {name: np.array(ids) for name, ids in groups.items()}


def gradient_vectors(model, terms, on_call=lambda: None):
    parameters = tuple(model.parameters())
    result = []
    for i, name in enumerate(TERMS):
        on_call()
        gradients = torch.autograd.grad(
            terms[name], parameters, retain_graph=i < len(TERMS) - 1, allow_unused=True
        )
        result.append(
            np.concatenate(
                [
                    np.zeros(parameter.numel())
                    if grad is None
                    else grad.detach().numpy().ravel().copy()
                    for parameter, grad in zip(parameters, gradients)
                ]
            )
        )
    result = np.stack(result)
    if not np.isfinite(result).all():
        raise ArithmeticError("Nonfinite diagnostic gradient")
    return result


def gradient_tables(vectors, values, groups):
    if (
        vectors.ndim != 2
        or vectors.shape[0] != len(TERMS)
        or not np.isfinite(vectors).all()
    ):
        raise ValueError("Invalid gradient matrix")
    data, physics, prior = vectors[0], vectors[1:8].mean(axis=0), vectors[8]
    total = data + physics + 0.001 * prior
    rows = [
        dict(
            term=name,
            value=float(values[name]),
            norm=norm(vector),
            **{group + "_norm": norm(vector[ids]) for group, ids in groups.items()},
        )
        for name, vector in zip(TERMS, vectors)
    ]
    balance = dict(
        total_norm=norm(total),
        physics_norm=norm(physics),
        data_norm=norm(data),
        weighted_prior_norm=0.001 * norm(prior),
    )
    for name, ids in groups.items():
        a, b = data[ids], physics[ids]
        denominator = norm(a) * norm(b)
        balance.update(
            {
                name + "_total_norm": norm(total[ids]),
                name + "_physics_norm": norm(b),
                name + "_data_norm": norm(a),
                name + "_data_physics_cosine": float(np.sum(a * b) / denominator)
                if denominator
                else None,
            }
        )
    return rows, balance, total


def set_vector(model, vector):
    vector = np.asarray(vector)
    if (
        vector.shape != (sum(p.numel() for p in model.parameters()),)
        or not np.isfinite(vector).all()
    ):
        raise ValueError("Invalid parameter vector")
    offset = 0
    with torch.no_grad():
        for parameter in model.parameters():
            end = offset + parameter.numel()
            parameter.copy_(
                torch.from_numpy(vector[offset:end]).reshape(parameter.shape)
            )
            offset = end


def get_vector(model):
    return np.concatenate(
        [parameter.detach().numpy().ravel().copy() for parameter in model.parameters()]
    )


def motion_decomposition(p, r, elastic, beta, observation, atol=1e-8):
    if (
        p.shape != r.shape
        or p.ndim != 2
        or p.shape[1] != 24
        or (len(p) - 1) % 64
        or len(p) < 65
        or elastic.shape != (len(p) - 1, 4)
        or beta.shape != (4,)
        or observation.shape != (4, 4)
    ):
        raise ValueError("Matched complete substep trajectories required")
    if not all(np.isfinite(v).all() for v in (p, r, elastic, beta, observation)):
        raise ValueError("Finite states and coefficients required")
    if (
        (beta <= 0).any()
        or (beta >= 1).any()
        or not np.array_equal(p[0], np.zeros(24))
        or not np.array_equal(r[0], np.zeros(24))
    ):
        raise ValueError(
            "Zero initial states and strictly stable motion factor required"
        )
    delta_p = p[:, 4:8] - r[:, 4:8]

    def residual(state):
        return (
            state[1:, :4]
            - state[:-1, :4]
            - beta * (state[1:, 4:8] + elastic - state[:-1, :4])
        )

    defect = residual(p) - residual(r)
    plastic, motion = np.zeros((len(p), 4)), np.zeros((len(p), 4))
    for k in range(1, len(p)):
        plastic[k] = (1 - beta) * plastic[k - 1] + beta * delta_p[k]
        motion[k] = (1 - beta) * motion[k - 1] + defect[k - 1]
    actual = p[:, :4] - r[:, :4]
    maximum = float(np.max(abs(plastic + motion - actual)))
    if maximum > atol:
        raise ArithmeticError(
            "Motion decomposition does not reconstruct the saved states"
        )
    daily = dict(
        plastic_driven_s=plastic[::64],
        motion_defect_s=motion[::64],
        background_difference=(p[:, 20:] - r[:, 20:])[::64],
        actual_s_difference=actual[::64],
        plastic_state_difference=delta_p[::64],
    )
    for key in ("plastic_driven_s", "motion_defect_s", "background_difference"):
        daily[key + "_observed"] = daily[key] @ observation.T
    daily["actual_observed"] = (
        daily["actual_s_difference"] + daily["background_difference"]
    ) @ observation.T
    daily["reconstructed_observed"] = sum(
        daily[key + "_observed"]
        for key in ("plastic_driven_s", "motion_defect_s", "background_difference")
    )
    observed_error = float(
        np.max(abs(daily["reconstructed_observed"] - daily["actual_observed"]))
    )
    if observed_error > atol or not all(np.isfinite(v).all() for v in daily.values()):
        raise ArithmeticError("Observation decomposition failed")
    return daily, dict(
        substeps=len(p) - 1,
        max_state_error_mm=maximum,
        max_observation_error_mm=observed_error,
    )
