"""Reproduce the v1.23 metadata audit without importing or evaluating models."""

import argparse
import ast
import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import zipfile

import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
PLAN = "docs/ootang_bplus_teacher_condition_review_plan.v1.23.md"
PLAN_SHA = "815dbe230a68686b963a5b779ebd7b657a97428180854d30cc29ac1952bf83a7"
OLD = "results/ootang_bplus_v1_22/20260911_input_transfer"
OLD_INDEX_SHA = "41ec19c5f4839b387d79beb01f1c15ad73461339e17639e205677d3c99346472"
PAIRS = "results/ootang_bplus_v1_21/20260911_balanced_origin"
PREFLIGHT = (
    "results/ootang_bplus_v1_1/20260910_implementation/source_pdf_preflight.json"
)
ZIP_ROOT = "section2d_v4/work/delivery_final/outang_repro/section2d_v4/"
ORIGINS = {342: (252,), 432: (252, 342), 612: (252, 342, 432)}
LEADS = (0, 6, 29, 59, 89, 119, 149, 179)
SOURCES = {
    "code/physics_guided/features.py": ["point_features", "dynamic_reference"],
    "code/physics_guided/models.py": ["M1.forward", "M1.predict", "M2.predict"],
    "code/physics_guided_history_learning/core.py": [
        "HistoryM1",
        "training_pairs",
        "history_values",
        "windows",
        "predict",
    ],
    "code/physics_guided_origin_learning/core.py": ["query_table", "make_samples"],
    "code/physics_guided_state_pinn/core.py": [
        "daily_features",
        "Case",
        "StatePINN.forward",
    ],
    "code/physics_guided_shared_mechanics/core.py": [
        "RateInputs",
        "RateReplay.forward",
    ],
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads((ROOT / path).read_text())


def guard():
    require(
        sha(ROOT / OLD / "artifact_manifest.json") == OLD_INDEX_SHA,
        "Changed v1.22 index",
    )
    index = read_json(f"{OLD}/artifact_manifest.json")["files"]
    actual = {
        str(p.relative_to(ROOT / OLD)) for p in (ROOT / OLD).rglob("*") if p.is_file()
    }
    require(
        actual == set(index) | {"artifact_manifest.json"}, "Changed v1.22 inventory"
    )
    protected = read_json(f"{OLD}/protected_before.json")
    protected.update(read_json(f"{OLD}/manifest.json")["sources"])
    for name, info in index.items():
        require(
            (ROOT / OLD / name).stat().st_size == info["bytes"], f"Changed size: {name}"
        )
        protected[f"{OLD}/{name}"] = info["sha256"]
    protected[f"{OLD}/artifact_manifest.json"] = OLD_INDEX_SHA
    protected[PLAN] = PLAN_SHA
    for name, expected in protected.items():
        require(sha(ROOT / name) == expected, f"Changed frozen source: {name}")
    return protected


def calendar(index):
    return (date(2016, 7, 1) + timedelta(days=int(index))).isoformat()


def array_sha(values):
    return digest(np.asarray(values, dtype="<f8").tobytes(order="C"))


def metadata_rows(h):
    with (ROOT / PAIRS / f"pairs_{h}.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    anchors = [
        (o, o + lead, 0) for o in range(31, h, 14) for lead in LEADS if o + lead < h
    ]
    paired = [(o, t, 1) for o in ORIGINS[h] for t in range(o, min(o + 180, h))]
    expected = anchors + paired
    require(len(rows) == len(expected), f"Unexpected row count: {h}")
    multiplicity = Counter(t for _, t, _ in paired)
    max_weight_error = 0.0
    for row, (o, t, block) in zip(rows, expected):
        wanted = dict(
            origin=o,
            target=t,
            lead=t - o,
            block=block,
            teacher_IN=h,
            teacher_OOF=h if block == 0 else o,
            last_observation_index=o - 1,
        )
        require(set(row) == set(wanted) | {"weight"}, "Unexpected CSV columns")
        for key, value in wanted.items():
            require(int(row[key]) == value, f"Sample rule differs: {h}, {key}")
            row[key] = value
        weight = (
            0.5 / len(anchors)
            if block == 0
            else 0.5 / (len(multiplicity) * multiplicity[t])
        )
        row["weight"] = float(row["weight"])
        error = abs(row["weight"] - weight)
        require(math.isfinite(error) and error <= 1e-15, "Sample weight differs")
        max_weight_error = max(max_weight_error, error)
    return rows, max_weight_error


def summarize(h, strategy, block, rows, teacher=None):
    origins = sorted({r["origin"] for r in rows})
    teachers = sorted({r[f"teacher_{strategy}"] for r in rows})
    targets = sorted({r["target"] for r in rows})
    return dict(
        fit_days=h,
        strategy=strategy,
        block=block,
        teacher=teacher if teacher else "all",
        rows=len(rows),
        teachers=len(teachers),
        origins=len(origins),
        unique_targets=len(targets),
        teacher_ids=";".join(map(str, teachers)),
        origin_indices=";".join(map(str, origins)),
        first_target=targets[0],
        last_target=targets[-1],
        first_target_date=calendar(targets[0]),
        last_target_date=calendar(targets[-1]),
        min_lead=min(r["lead"] for r in rows),
        max_lead=max(r["lead"] for r in rows),
        total_weight=math.fsum(r["weight"] for r in rows),
    )


def collect():
    protected = guard()
    sources = {}
    blocks, teachers, checks = [], [], []
    slots = 0
    for h in ORIGINS:
        rows, error = metadata_rows(h)
        checks.append(
            dict(
                fit_days=h,
                independently_enumerated_rows=len(rows),
                max_weight_error=error,
            )
        )
        sources[f"{PAIRS}/pairs_{h}.csv"] = sha(ROOT / PAIRS / f"pairs_{h}.csv")
        for strategy in ("IN", "OOF"):
            for block in (0, 1):
                group = [r for r in rows if r["block"] == block]
                blocks.append(summarize(h, strategy, block, group))
                for teacher in sorted({r[f"teacher_{strategy}"] for r in group}):
                    teachers.append(
                        summarize(
                            h,
                            strategy,
                            block,
                            [r for r in group if r[f"teacher_{strategy}"] == teacher],
                            teacher,
                        )
                    )
            # Metadata only: training queries and the v1.22 diagnostic forecast range.
            query_pairs = [(r["origin"], r["target"]) for r in rows]
            query_pairs += [(h, t) for t in range(h, h + (90 if h == 342 else 180))]
            for o, t in query_pairs:
                require(31 <= o <= h and o <= t < h + 180, "Unavailable query")
                for k in range(30):
                    physical_day, observed_day = t - 29 + k, o - 30 + k
                    require(
                        0 <= observed_day < o
                        and physical_day - observed_day == t - o + 1,
                        "Window time offset differs",
                    )
                    slots += 1

    origins_support = []
    for h in ORIGINS:
        for strategy in ("IN", "OOF"):
            group = [
                r for r in teachers if r["fit_days"] == h and r["strategy"] == strategy
            ]
            paired_ids = {r["teacher"] for r in group if r["block"] == 1}
            origins_support.append(
                dict(
                    fit_days=h,
                    strategy=strategy,
                    current_teacher_has_paired_rows=h in paired_ids,
                    paired_teachers=len(paired_ids),
                    paired_origins=len(ORIGINS[h]),
                    max_origins_per_paired_teacher=max(
                        r["origins"] for r in group if r["block"] == 1
                    ),
                )
            )

    registry = {}
    for path, symbols in SOURCES.items():
        raw = (ROOT / path).read_bytes()
        require(
            path in protected and digest(raw) == protected[path],
            f"Unprotected code: {path}",
        )
        tree = ast.parse(raw)
        found = {}
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                found[node.name] = node
                if isinstance(node, ast.ClassDef):
                    for child in node.body:
                        if isinstance(child, ast.FunctionDef):
                            found[f"{node.name}.{child.name}"] = child
        registry[path] = dict(
            sha256=digest(raw),
            symbols={
                symbol: dict(start=found[symbol].lineno, end=found[symbol].end_lineno)
                for symbol in symbols
            },
        )
        sources[path] = digest(raw)

    pdf = read_json(PREFLIGHT)
    require(
        pdf["verdict"] == "PASS" and sha(ROOT / "manuscript.pdf") == pdf["sha256"],
        "Prior PDF preflight does not cover current bytes",
    )
    with pymupdf.open(ROOT / "manuscript.pdf") as document:
        require(
            len(document) == pdf["reader_page_count"] == 26, "PDF page count differs"
        )
        pages = [
            dict(
                page=page,
                characters=len(document[page - 1].get_text()),
                text_sha256=digest(document[page - 1].get_text().encode()),
            )
            for page in (4, 11, 19, 20)
        ]
    sources[PREFLIGHT] = sha(ROOT / PREFLIGHT)
    sources["manuscript.pdf"] = pdf["sha256"]
    sources["section2d_v4.zip"] = sha(ROOT / "section2d_v4.zip")
    with zipfile.ZipFile(ROOT / "section2d_v4.zip") as archive:
        lock = json.loads(archive.read(ZIP_ROOT + "results/calibration_lock.json"))
        for member, expected in lock.items():
            require(
                digest(archive.read(ZIP_ROOT + member)) == expected,
                f"ZIP lock differs: {member}",
            )
        members = {
            member: digest(archive.read(ZIP_ROOT + member))
            for member in (
                "physical_model.py",
                "predict.py",
                "results/protocol.json",
                "results/calibrated.json",
                "results/calibration_lock.json",
            )
        }
        calibrated = json.loads(archive.read(ZIP_ROOT + "results/calibrated.json"))
        protocol = json.loads(archive.read(ZIP_ROOT + "results/protocol.json"))
    names = calibrated["names"]
    require(
        len(names)
        == len(set(names))
        == len(calibrated["theta"])
        == protocol["parameter_count"]
        == 54,
        "Original parameter identity differs",
    )
    parameters = [
        dict(index=i, name=name, storage="log" if name.startswith("log_") else "linear")
        for i, name in enumerate(names)
    ]
    sources[f"{PAIRS}/teachers.json"] = sha(ROOT / PAIRS / "teachers.json")
    teacher_records = read_json(f"{PAIRS}/teachers.json")
    require(set(teacher_records) == {"252", "342", "432", "612"}, "Unexpected teachers")
    identities = []
    for key, value in teacher_records.items():
        n, recipe, record = int(key), value["recipe"], value["record"]
        source = record["source"]
        require(sha(ROOT / source) == record["source_sha256"], f"Changed teacher: {n}")
        final = read_json(source)["theta"]
        name = (
            f"inner_432_252_{recipe}"
            if n == 252
            else f"inner_612_342_{recipe}"
            if n == 342
            else f"outer_{n}_{recipe}"
        )
        trajectory = f"results/ootang_bplus_v1_4/20260911_selection/{name}.npz"
        with np.load(ROOT / trajectory, allow_pickle=False) as saved:
            theta = saved["theta"]  # No trajectory/state arrays or model calls.
        require(
            theta.shape == (54,) and np.isfinite(theta).all(), "Invalid teacher vector"
        )
        require(
            np.array_equal(theta, record["theta"]) and np.array_equal(theta, final),
            "Saved/final teacher vectors differ",
        )
        sources[source], sources[trajectory] = (
            sha(ROOT / source),
            sha(ROOT / trajectory),
        )
        identities.append(
            dict(
                fit_days=n,
                recipe=recipe,
                parameter_count=54,
                theta_float64_le_sha256=array_sha(theta),
                source=source,
                source_sha256=sources[source],
                trajectory=trajectory,
                exact_final_record_match=True,
                exact_trajectory_match=True,
            )
        )
    for path, expected in sources.items():
        require(
            path in protected and protected[path] == expected,
            f"Source outside frozen chain: {path}",
        )
    require(guard() == protected, "Protected sources changed during audit")
    return {
        "coverage_by_block.csv": blocks,
        "coverage_by_teacher.csv": teachers,
        "paired_support.csv": origins_support,
        "parameter_names.csv": parameters,
        "teacher_identity.json": identities,
        "window_offsets.csv": [
            dict(
                lead=lead,
                physical_first=lead - 29,
                physical_last=lead,
                observed_first=-30,
                observed_last=-1,
                delta_left_endpoint=-31,
                matched_slot_offset=lead + 1,
            )
            for lead in range(180)
        ],
        "source_registry.json": dict(
            plan=PLAN,
            plan_sha256=PLAN_SHA,
            files=sources,
            code_symbols=registry,
            zip_root=ZIP_ROOT,
            zip_members=members,
        ),
        "mentor_sources.json": dict(
            pdf_preflight_reused=PREFLIGHT,
            preflight_scope="structure_only",
            pdf=pdf,
            current_page_count=26,
            text_parser=f"pymupdf {pymupdf.VersionBind}",
            pages_read=pages,
            zip_lock_members_checked=len(lock),
            original_protocol=protocol,
            original_final_theta_float64_le_sha256=array_sha(calibrated["theta"]),
            original_final_theta_used_as_development_teacher=False,
        ),
        "verification.json": dict(
            passed=True,
            independently_enumerated_samples=checks,
            window_slot_checks=slots,
            protected_files_checked=len(protected),
            parameter_vectors_checked=4,
            zero_new_model_evaluations=True,
            zero_new_fits=True,
            raw_observation_as_of_verified="unknown",
            scope="Metadata enumeration, source identity and window dates; no effectiveness evaluation",
        ),
    }


def encoded(name, value):
    if name.endswith(".csv"):
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(value[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(value)
        return stream.getvalue().encode()
    return (
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    artifacts = {name: encoded(name, value) for name, value in collect().items()}
    artifacts["audit_source.py"] = Path(__file__).read_bytes()
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        for name, value in artifacts.items():
            with (args.output / name).open("xb") as stream:
                stream.write(value)
        index = {
            name: dict(sha256=digest(value), bytes=len(value))
            for name, value in artifacts.items()
        }
        with (args.output / "artifact_manifest.json").open("xb") as stream:
            stream.write(encoded("index.json", dict(files=index)))
    else:
        expected = set(artifacts) | {"artifact_manifest.json"}
        require(
            {p.name for p in args.verify.iterdir()} == expected,
            "Changed audit inventory",
        )
        index = json.loads((args.verify / "artifact_manifest.json").read_text())[
            "files"
        ]
        require(set(index) == set(artifacts), "Changed index inventory")
        for name, value in artifacts.items():
            require(
                (args.verify / name).read_bytes() == value,
                f"Audit replay differs: {name}",
            )
            require(
                index[name] == dict(sha256=digest(value), bytes=len(value)),
                f"Audit index differs: {name}",
            )
    print(json.dumps(json.loads(artifacts["verification.json"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
