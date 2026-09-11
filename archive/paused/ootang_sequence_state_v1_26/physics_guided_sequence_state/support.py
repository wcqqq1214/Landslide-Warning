"""Pinned successful parent, frozen evaluation inputs and checkpoint identities."""

import numpy as np
import pandas as pd
import torch

from physics_guided_forecast_error.artifacts import ROOT, check_hashes, check_index, read_json, sha
from physics_guided_history_learning.core import evaluation_pairs, pair_table

CONFIG = ROOT / "config/ootang_bplus_sequence_state_diagnostics.v1_26.json"
CONFIG_SHA = "bcbe272adb93ef9171b39c77b9861476e48d5ed6e3c87832308b348dcf6801de"
PLAN_SHA = "438c8a0466ffd655a6c0f3223758b24ed536fdab32ef53e9598b145e263c832a"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def specification():
    spec = read_json(CONFIG)
    require(sha(CONFIG) == CONFIG_SHA and sha(ROOT / spec["protocol"]) == PLAN_SHA,
            "Changed frozen-state diagnostic protocol")
    return spec


def source_guard(spec):
    source = ROOT / spec["source_run"]
    require(sha(source / "artifact_manifest.json") == spec["source_index_sha256"], "Changed parent archive")
    check_index(source)
    require(sha(ROOT / spec["source_verification"]) == spec["source_verification_sha256"]
            and read_json(source / "verification.json") == read_json(ROOT / spec["source_verification"])
            and read_json(source / "verification.json")["passed"]
            and read_json(source / "launcher.json")["exitcode"] == 0
            and read_json(source / "completed.json")["execution_complete"], "Parent did not pass its sealed verification")
    protected = read_json(source / "protected_before.json")
    protected.update(read_json(source / "manifest.json")["sources"])
    protected.update({str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()})
    protected[spec["source_verification"]] = spec["source_verification_sha256"]
    for name in ("docs/ootang_bplus_sequence_learning_results.v1.25.md",
                 "docs/ootang_bplus_sequence_learning_implementation.v1.25.md"):
        protected[name] = sha(ROOT / name)
    check_hashes(protected)
    return protected


def arrays(path):
    with np.load(path, allow_pickle=False) as saved:
        return {key: saved[key].copy() for key in saved.files}


def table(path):
    return pd.read_csv(path, float_precision="round_trip")


def evaluation(source, spec, h):
    require(h in spec["prefixes"], "Unregistered diagnostic prefix")
    data = arrays(source / f"evaluation_{h}.npz")
    require(set(data) == {"encoder", "decoder", "baseline", "row_batch", "row_lead",
                           "weights", "blocks", "groups", "lengths"}, "Changed prediction input keys")
    queries = pair_table(evaluation_pairs(h, h+180), h)
    pd.testing.assert_frame_equal(table(source / f"queries_{h}.csv"), queries, check_exact=True)
    origins = list(dict.fromkeys(queries.origin))
    groups = np.array([(h, int(o)) for o in origins])
    np.testing.assert_array_equal(data["groups"], groups)
    require(len(groups) == spec["evaluation_sequences"][str(h)], "Changed sequence count")
    np.testing.assert_array_equal(data["row_batch"], [origins.index(o) for o in queries.origin])
    np.testing.assert_array_equal(data["row_lead"], queries.lead)
    np.testing.assert_array_equal(data["weights"], np.zeros(len(queries)))
    np.testing.assert_array_equal(data["blocks"], np.full(len(queries), -1))
    lengths = [int(queries.loc[queries.origin == o, "lead"].max())+1 for o in origins]
    np.testing.assert_array_equal(data["lengths"], lengths)
    require(data["row_batch"].dtype.kind in "iu" and data["row_lead"].dtype.kind in "iu", "Noninteger query indices")
    base = arrays(source / f"mean_{h}_P0.npz")["means"]
    require(base.shape == (1, h+180, 4) and np.isfinite(base).all(), "Changed B+ baseline")
    for key, shape in (("encoder", (len(groups), 30, 23, 1, 4)),
                       ("decoder", (len(groups), 180, 23, 1, 4)),
                       ("baseline", (len(groups), 180, 4))):
        require(data[key].shape == shape and data[key].dtype == np.float64
                and np.isfinite(data[key]).all(), "Invalid frozen prediction array")
    require(np.count_nonzero(data["decoder"][:, :, 20:22]) == 0, "Future observations are not masked")
    for b, ((_, origin), length) in enumerate(zip(groups, lengths)):
        np.testing.assert_array_equal(data["baseline"][b, :length], base[0, origin:origin+length])
        require(not np.count_nonzero(data["baseline"][b, length:])
                and not np.count_nonzero(data["decoder"][b, length:]), "Changed zero padding")
        np.testing.assert_array_equal(data["encoder"][b, :, 22, 0], np.broadcast_to(np.arange(-30, 0)[:, None]/179, (30, 4)))
        np.testing.assert_array_equal(data["decoder"][b, :length, 22, 0], np.broadcast_to(np.arange(length)[:, None]/179, (length, 4)))
    return dict(raw=data, base=base[0], targets=queries.target.to_numpy(),
                future_group=origins.index(h), encoder=torch.tensor(data["encoder"]),
                decoder=torch.tensor(data["decoder"]))
