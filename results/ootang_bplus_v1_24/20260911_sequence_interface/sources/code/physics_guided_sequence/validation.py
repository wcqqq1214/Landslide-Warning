"""Fixed untrained interface probes, independent inputs and numerical references."""

import math

import numpy as np
import pandas as pd
import torch

from audit_ootang_teacher_conditions_v1_23 import guard as previous_guard
from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    load_observations,
    read_json,
    sha,
)
from physics_guided_history_diagnostics.workflow import saved_scalers
from physics_guided_origin_learning.support import read_teachers
from .core import Budget, initial_models, make_sequence
from .reference import inputs as reference_inputs, predict as reference_predict

CONFIG = ROOT / "config/ootang_bplus_sequence_interface.v1_24.json"
CONFIG_SHA = "949b4a4e3eda9d96c697b0a56c159c583eedf31fe08ff9493acac019c2555bd8"
PLAN_SHA = "4bb04fdf95805ae94ab9ae577569acc42270e8dfce37e7505bc79ee975e16f73"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def specification():
    spec = read_json(CONFIG)
    require(
        sha(CONFIG) == CONFIG_SHA and sha(ROOT / spec["protocol"]) == PLAN_SHA,
        "Changed sequence interface protocol",
    )
    return spec


def source_guard(spec):
    protected = previous_guard()
    source = ROOT / spec["source_run"]
    require(
        sha(source / "artifact_manifest.json") == spec["source_index_sha256"],
        "Changed teacher review index",
    )
    check_index(source)
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()}
    )
    for path in (
        "code/audit_ootang_teacher_conditions_v1_23.py",
        "docs/ootang_bplus_teacher_condition_review.v1.23.md",
    ):
        protected[path] = sha(ROOT / path)
    check_hashes(protected)
    return protected


def input_check(sequence, expected):
    count = 0
    for value, reference in zip(
        (sequence.encoder, sequence.decoder, sequence.baseline), expected
    ):
        np.testing.assert_array_equal(value.numpy(), reference)
        count += reference.size
    return count


def prepare(spec):
    source = ROOT / spec["learning_source"]
    cases, fingerprints, values_checked = [], [], 0
    for h in spec["prefixes"]:
        _, _, labels = load_observations(source / "input.csv", h)
        pool = read_teachers(source / "input.csv", ROOT / spec["physical_source"], h)
        scalers = saved_scalers(source, h)
        constants = read_json(source / f"constants_{h}.json")
        require(
            array_sha(labels) == constants["labels_sha256"]
            and array_sha(pool[h][1]) == constants["feature_sha256"],
            "Changed frozen inputs",
        )
        table = pd.read_csv(source / f"pairs_{h}.csv")
        for strategy in ("IN", "OOF"):
            for row in table.to_dict("records"):
                origin, target = int(row["origin"]), int(row["target"])
                teacher, horizon = int(row[f"teacher_{strategy}"]), target - origin + 1
                base, features = pool[teacher]
                sequence = make_sequence(
                    base, features, labels[:origin], origin, horizon, *scalers
                )
                values_checked += input_check(
                    sequence,
                    reference_inputs(
                        base, features, labels[:origin], origin, horizon, *scalers
                    ),
                )
                fingerprints.append(
                    dict(
                        fit_days=h,
                        strategy=strategy,
                        block=int(row["block"]),
                        origin=origin,
                        target=target,
                        teacher=teacher,
                        horizon=horizon,
                        encoder_sha256=array_sha(sequence.encoder.numpy()),
                        decoder_sha256=array_sha(sequence.decoder.numpy()),
                        baseline_sha256=array_sha(sequence.baseline.numpy()),
                    )
                )
        descriptors = [("CURRENT", h, h, 180)]
        descriptors += [
            (strategy, o, h if strategy == "IN" else o, min(180, h - o))
            for strategy in ("IN", "OOF")
            for o in spec["paired_origins"][str(h)]
        ]
        for strategy, origin, teacher, horizon in descriptors:
            base, features = pool[teacher]
            sequence = make_sequence(
                base, features, labels[:origin], origin, horizon, *scalers
            )
            values_checked += input_check(
                sequence,
                reference_inputs(
                    base, features, labels[:origin], origin, horizon, *scalers
                ),
            )
            cases.append(
                (
                    dict(
                        name=f"h{h}_{strategy}_o{origin}",
                        fit_days=h,
                        strategy=strategy,
                        origin=origin,
                        teacher=teacher,
                        horizon=horizon,
                    ),
                    sequence,
                )
            )
    require(
        len(fingerprints) == 3030 and len(cases) == 15,
        "Unexpected fixed interface cases",
    )
    return cases, pd.DataFrame(fingerprints), values_checked


def direction(shape):
    result = torch.sin(torch.arange(math.prod(shape), dtype=torch.float64) + 1).reshape(
        shape
    )
    return result / torch.linalg.vector_norm(result)


def norm(tensor):
    return math.hypot(*tensor.detach().numpy().reshape(-1).tolist())


def probe_case(info, sequence, zero, probe, budget, spec):
    encoder = sequence.encoder.clone().requires_grad_(True)
    decoder = sequence.decoder
    with torch.no_grad():
        zero_mean = sequence.baseline + zero(encoder, decoder, budget)
    require(
        torch.equal(zero_mean, sequence.baseline), "Zero correction did not preserve B+"
    )
    output = probe(encoder, decoder, budget)
    reference = reference_predict(
        probe.state_dict(), encoder.detach().numpy(), decoder.numpy(), budget
    )
    np.testing.assert_allclose(
        output.detach().numpy(),
        reference,
        atol=spec["reference_atol_mm"],
        rtol=spec["reference_rtol"],
    )
    with torch.no_grad():
        prefix = probe(encoder, decoder[:, :30], budget)
        changed = decoder.clone()
        changed[:, 30:, :20] += spec["suffix_physical_perturbation"]
        changed_output = probe(encoder, changed, budget)
        state = probe.encode(encoder, budget)
        first, state = probe.decode(decoder[:, :30], state, budget)
        last, _ = probe.decode(decoder[:, 30:], state, budget)
    require(torch.equal(prefix, output[:, :30]), "Short prediction prefix differs")
    require(
        torch.equal(changed_output[:, :30], output[:, :30]),
        "Future suffix affected earlier output",
    )
    require(
        torch.equal(torch.cat([first, last], dim=1), output),
        "Decoder state did not survive segmentation",
    )

    history_direction = torch.zeros_like(encoder)
    history_direction[:, :, 20:22] = direction(encoder[:, :, 20:22].shape)
    weight_direction = direction(probe.gates.weight.shape)
    gradients, derivatives = [], {}
    for lead in (0, 29, info["horizon"] - 1):
        for point, station in enumerate(spec["point_order"]):
            budget.tick("gradient_calls")
            history_grad, weight_grad = torch.autograd.grad(
                output[0, lead, point], (encoder, probe.gates.weight), retain_graph=True
            )
            require(
                torch.isfinite(history_grad).all()
                and torch.isfinite(weight_grad).all(),
                "Nonfinite sequence gradients",
            )
            size = norm(history_grad[:, :, 20:22])
            if lead == 0:
                require(
                    size > 0, f"No first-day history path: {info['name']}/{station}"
                )
                derivatives[point] = (
                    float((history_grad * history_direction).sum()),
                    float((weight_grad * weight_direction).sum()),
                )
            gradients.append(
                dict(
                    **info,
                    lead=lead,
                    station=station,
                    history_gradient_norm=size,
                    gate_gradient_norm=norm(weight_grad),
                )
            )

    epsilon = spec["finite_difference_epsilon"]
    with torch.no_grad():
        plus = probe(encoder + epsilon * history_direction, decoder[:, :1], budget)
        minus = probe(encoder - epsilon * history_direction, decoder[:, :1], budget)
        history_fd = ((plus - minus) / (2 * epsilon))[0, 0].numpy()
        saved_weight = probe.gates.weight.detach().clone()
        try:
            probe.gates.weight.copy_(saved_weight + epsilon * weight_direction)
            plus = probe(encoder, decoder[:, :1], budget)
            probe.gates.weight.copy_(saved_weight - epsilon * weight_direction)
            minus = probe(encoder, decoder[:, :1], budget)
            weight_fd = ((plus - minus) / (2 * epsilon))[0, 0].numpy()
        finally:
            probe.gates.weight.copy_(saved_weight)
    require(torch.equal(probe.gates.weight, saved_weight), "Probe weight changed")
    differences = []
    for point, station in enumerate(spec["point_order"]):
        for component, fd in enumerate((history_fd, weight_fd)):
            derivative = derivatives[point][component]
            error = abs(float(fd[point]) - derivative)
            limit = spec["finite_difference_atol"] + spec[
                "finite_difference_rtol"
            ] * abs(derivative)
            require(
                math.isfinite(error) and error <= limit,
                f"Directional derivative failed: {info['name']}/{station}/{component}, error={error}, limit={limit}",
            )
            differences.append(
                dict(
                    **info,
                    station=station,
                    direction=("history", "gates")[component],
                    autograd=derivative,
                    central_difference=float(fd[point]),
                    absolute_error=error,
                    allowed_error=limit,
                )
            )
    arrays = dict(
        encoder=sequence.encoder.numpy(),
        decoder=decoder.numpy(),
        baseline=sequence.baseline.numpy(),
        zero_mean=zero_mean.numpy(),
        probe_correction=output.detach().numpy(),
        numpy_correction=reference,
    )
    record = dict(
        **info,
        zero_mean_exact=True,
        short_prefix_exact=True,
        suffix_isolated=True,
        segmented_decoder_exact=True,
        max_reference_difference_mm=float(
            np.max(abs(arrays["probe_correction"] - reference))
        ),
    )
    return arrays, record, gradients, differences


def validate(spec, callback=None):
    torch.set_num_threads(spec["cpu_threads"])
    torch.use_deterministic_algorithms(True)
    budget = Budget(spec["limits_per_validation"])
    cases, fingerprints, values_checked = prepare(spec)
    zero, probe = initial_models(spec["seed"])
    require(
        sum(p.numel() for p in probe.parameters()) == spec["model_parameters"],
        "Changed parameter count",
    )
    checkpoints = {
        name: {key: value.detach().clone() for key, value in model.state_dict().items()}
        for name, model in (("zero", zero), ("probe", probe))
    }
    arrays, records, gradients, differences = {}, [], [], []
    for info, sequence in cases:
        data, record, grad, fd = probe_case(info, sequence, zero, probe, budget, spec)
        arrays[info["name"]] = data
        records.append(record)
        gradients.extend(grad)
        differences.extend(fd)
        if callback:
            callback(info, data, record, grad, fd, dict(budget.counts))
    for name, model in (("zero", zero), ("probe", probe)):
        require(
            all(
                torch.equal(value, checkpoints[name][key])
                for key, value in model.state_dict().items()
            ),
            "An untrained checkpoint changed",
        )
    require(
        budget.counts == spec["expected_counts_per_validation"],
        "Scientific counts differ from registration",
    )
    summary = dict(
        passed=True,
        cases=len(cases),
        source_queries=len(fingerprints),
        input_values_checked=values_checked,
        model_parameters=spec["model_parameters"],
        counts=budget.counts,
        neural_updates=0,
        physical_calls=0,
        scaler_fits=0,
        probability_scale_fits=0,
        raw_observation_as_of_verified="unknown",
        effectiveness_evaluated=False,
    )
    return dict(
        arrays=arrays,
        records=pd.DataFrame(records),
        gradients=pd.DataFrame(gradients),
        differences=pd.DataFrame(differences),
        fingerprints=fingerprints,
        checkpoints=checkpoints,
        summary=summary,
    )
