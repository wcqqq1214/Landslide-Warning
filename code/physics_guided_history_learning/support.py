"""Pinned protocol and source inventory for the finite learning comparison."""

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
    sha,
)

CONFIG = ROOT / "config/ootang_bplus_history_learning.v1_17.json"
CONFIG_SHA = "e34dc46f265cd86cf1a0f086f38e6978229c74fcbe00c171e5f6ab15a67e462b"
PLAN_SHA = "23ef8e5091d0876d363de74f7a9b326194da3d11e2e8df4dfe042cce9565bdb7"


def specification():
    spec = read_json(CONFIG)
    if sha(CONFIG) != CONFIG_SHA or sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Registered history learning design changed")
    return spec


def source_guard(spec):
    source = ROOT / spec["history_source"]
    if sha(source / "artifact_manifest.json") != spec["history_index_sha256"]:
        raise ValueError("Frozen history audit index differs")
    check_index(source)
    verification = read_json(source / "verification.json")
    if (
        not verification["passed"]
        or not verification["published_source_equivalence_passed"]
        or verification["original_three_source_equivalence_passed"]
        or verification["raw_observation_as_of_verified"] != "unknown"
    ):
        raise ValueError("History construction and upstream uncertainty records differ")
    protected = read_json(source / "protected_before.json")
    protected.update(read_json(source / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()}
    )
    name = "docs/ootang_bplus_history_availability_results.v1.16.md"
    protected[name] = sha(ROOT / name)
    for name in ("physical_source", "learning_source"):
        check_index(ROOT / spec[name])
    check_hashes(protected)
    return protected
