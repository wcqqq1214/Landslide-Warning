"""Pinned paired sources including the preserved gradient warning and scalar audit."""

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
    sha,
)
from physics_guided_origin_learning.support import (
    RECIPES as RECIPES,
    read_teachers as read_teachers,
)

CONFIG = ROOT / "config/ootang_bplus_balanced_origin.v1_21.json"
CONFIG_SHA = "2dfa103e249369407c605462953d3c80b4f2cb63ca3322c68964a5a29ba1d9c1"
PLAN_SHA = "f21a8657b3e4a9f1edbb3978d0d94784eada3d802c4e4ace0b2887da3d43f0c8"


def specification():
    spec = read_json(CONFIG)
    if sha(CONFIG) != CONFIG_SHA or sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Registered balancing design changed")
    return spec


def source_guard(spec):
    source = ROOT / spec["diagnostic_source"]
    if sha(source / "artifact_manifest.json") != spec["diagnostic_index_sha256"]:
        raise ValueError("Frozen gradient diagnostic index differs")
    check_index(source)
    if (
        not read_json(source / "verification.json")["passed"]
        or read_json(source / "launcher.json")["exitcode"] != 0
    ):
        raise ValueError("Frozen gradient diagnostic did not pass")
    protected = read_json(source / "protected_before.json")
    protected.update(read_json(source / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()}
    )
    for name in (
        "docs/ootang_bplus_origin_gradients_results.v1.20.md",
        "results/ootang_bplus_v1_20/scalar_geometry_check.py",
        "results/ootang_bplus_v1_20/scalar_geometry_check.json",
    ):
        protected[name] = sha(ROOT / name)
    receipt = read_json(source.parent / "scalar_geometry_check.json")
    if (
        not receipt["scalar_geometry_passed"]
        or receipt["source_index_sha256"] != spec["diagnostic_index_sha256"]
        or receipt["checker_sha256"] != sha(source.parent / "scalar_geometry_check.py")
    ):
        raise ValueError("Preserved scalar check differs")
    equal = ROOT / spec["equal_source"]
    if sha(equal / "artifact_manifest.json") != spec["equal_index_sha256"]:
        raise ValueError("Frozen equal-weight reference differs")
    check_index(equal)
    for key in ("history_learning_source", "physical_source", "teacher_source"):
        check_index(ROOT / spec[key])
    teachers = read_json(ROOT / spec["teacher_source"] / "teachers.json")
    if {int(k): v["recipe"] for k, v in teachers.items()} != RECIPES:
        raise ValueError("Frozen own-J0 teacher selection changed")
    check_hashes(protected)
    return protected
