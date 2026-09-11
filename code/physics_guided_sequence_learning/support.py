"""Frozen source identities, prefix-only packing and original-query fingerprints."""

from collections import Counter

import numpy as np
import pandas as pd

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
from physics_guided_history_learning.core import evaluation_pairs, pair_table
from physics_guided_origin_learning.support import read_teachers
from physics_guided_sequence.reference import inputs as independent_inputs
from .core import make_batch

CONFIG = ROOT / "config/ootang_bplus_sequence_learning.v1_25.json"
CONFIG_SHA = "d5e212edf28cfe5560c11a8a139adc69bdcfde6b9f67167b59cc738e3461a3e9"
PLAN_SHA = "a57953ee83f5f814eab2e4644d165b7db43e6a00e83ea2f416c39530d66741df"
LEARNING_INDEX = "92e834158a3a1fd9e57552e386718f3e15f8d572bfa9b1ef75103f88ab8c026b"
RECEIPT = "results/ootang_bplus_v1_24/20260911_sequence_interface_verification.json"
RECEIPT_SHA = "4d425d56bad1184a85eec9e40ad93ee8fbfd6b74a8b0ac4585b8f12e7659abff"
STRATEGIES = ("IN_CARRY", "IN_RESET", "OOF_CARRY", "OOF_RESET")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_table(path):
    return pd.read_csv(path, float_precision="round_trip")


def specification():
    spec = read_json(CONFIG)
    require(
        sha(CONFIG) == CONFIG_SHA and sha(ROOT / spec["protocol"]) == PLAN_SHA,
        "Changed registered sequence learning design",
    )
    return spec


def source_guard(spec):
    source = ROOT / spec["interface_source"]
    require(
        sha(source / "artifact_manifest.json") == spec["interface_index_sha256"],
        "Changed sequence interface archive",
    )
    check_index(source)
    require(
        sha(ROOT / RECEIPT) == RECEIPT_SHA
        and read_json(ROOT / RECEIPT)["result"]["passed"]
        and read_json(source / "launcher.json")["exitcode"] == 0,
        "Interface verification receipt differs",
    )
    protected = read_json(source / "protected_before.json")
    protected.update(read_json(source / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()}
    )
    protected[RECEIPT] = RECEIPT_SHA
    path = "docs/ootang_bplus_sequence_interface_results.v1.24.md"
    protected[path] = sha(ROOT / path)
    learning = ROOT / spec["learning_source"]
    require(
        sha(learning / "artifact_manifest.json") == LEARNING_INDEX,
        "Changed frozen learning reference",
    )
    check_index(learning)
    check_index(ROOT / spec["physical_source"])
    check_hashes(protected)
    return protected


def original_table(h):
    anchors = [
        (o, o + lead, 0)
        for o in range(31, h, 14)
        for lead in (0, 6, 29, 59, 89, 119, 149, 179)
        if o + lead < h
    ]
    paired = [
        (o, t, 1) for o in (252, 342, 432) if o < h for t in range(o, min(o + 180, h))
    ]
    counts = Counter(t for _, t, _ in paired)
    return pd.DataFrame(
        [
            dict(
                origin=o,
                target=t,
                lead=t - o,
                block=b,
                teacher_IN=h,
                teacher_OOF=h if b == 0 else o,
                last_observation_index=o - 1,
                weight=0.5 / len(anchors)
                if b == 0
                else 0.5 / (len(counts) * counts[t]),
            )
            for o, t, b in anchors + paired
        ]
    )


def check_batch_inputs(batch, pool, labels, scalers):
    count = 0
    for b, ((teacher, origin), horizon) in enumerate(zip(batch.groups, batch.lengths)):
        expected = independent_inputs(
            *pool[teacher], labels[:origin], origin, horizon, *scalers
        )
        for actual, reference in zip(
            (
                batch.encoder[b : b + 1],
                batch.decoder[b : b + 1, :horizon],
                batch.baseline[b : b + 1, :horizon],
            ),
            expected,
        ):
            np.testing.assert_array_equal(actual.numpy(), reference)
            count += reference.size
        require(
            not batch.decoder[b, horizon:].count_nonzero()
            and not batch.baseline[b, horizon:].count_nonzero(),
            "Nonzero padding",
        )
    return count


def fingerprints(h, strategy, table, batch):
    rows = []
    for i, row in enumerate(table.to_dict("records")):
        b, length = int(batch.row_batch[i]), int(batch.row_lead[i]) + 1
        rows.append(
            dict(
                fit_days=h,
                strategy=strategy,
                block=row["block"],
                origin=row["origin"],
                target=row["target"],
                teacher=row[f"teacher_{strategy}"],
                horizon=length,
                encoder_sha256=array_sha(batch.encoder[b : b + 1].numpy()),
                decoder_sha256=array_sha(batch.decoder[b : b + 1, :length].numpy()),
                baseline_sha256=array_sha(batch.baseline[b : b + 1, :length].numpy()),
            )
        )
    return pd.DataFrame(rows)


def prepare_prefix(spec, path, h):
    require(h in spec["prefixes"], "Unregistered prefix")
    source = ROOT / spec["learning_source"]
    _, _, labels = load_observations(path, h)
    pool = read_teachers(path, ROOT / spec["physical_source"], h)
    scalers = saved_scalers(source, h)
    constants = read_json(source / f"constants_{h}.json")
    require(
        array_sha(labels) == constants["labels_sha256"]
        and array_sha(pool[h][1]) == constants["feature_sha256"],
        "Changed prefix inputs",
    )
    table = read_table(source / f"pairs_{h}.csv")
    pd.testing.assert_frame_equal(table, original_table(h), check_exact=True)
    batches, prints, count = {}, [], 0
    for strategy in spec["sources"]:
        batch = make_batch(table, pool, labels, scalers, f"teacher_{strategy}", True)
        require(
            len(batch.groups) == spec["packed_counts"][str(h)]
            and batch.decoder.shape[1] == 180,
            "Registered packed shape differs",
        )
        batches[strategy] = batch
        count += check_batch_inputs(batch, pool, labels, scalers)
        prints.append(fingerprints(h, strategy, table, batch))
    prints = pd.concat(prints, ignore_index=True)
    old = read_table(ROOT / spec["interface_source"] / "fingerprints.csv")
    pd.testing.assert_frame_equal(
        prints, old[old.fit_days == h].reset_index(drop=True), check_exact=True
    )
    queries = pair_table(evaluation_pairs(h, h + 180), h)
    evaluation = make_batch(queries, pool, labels, scalers, "teacher_fit_days", False)
    count += check_batch_inputs(evaluation, pool, labels, scalers)
    require(
        evaluation.targets is None
        and len(evaluation.groups) == {342: 3, 432: 4, 612: 5}[h],
        "Prediction batch identity differs",
    )
    return dict(
        labels=labels,
        pool=pool,
        pairs=table,
        queries=queries,
        batches=batches,
        evaluation=evaluation,
        fingerprints=prints,
        input_values_checked=count,
    )
