"""Post-warning scalar check of sealed geometry; no network calls or refitting.

Run from any directory. --record creates one exclusive adjacent JSON receipt.
The original scientific archive, warnings, and numerical tolerances stay intact.
"""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

SOURCE = Path(__file__).resolve().parent / "20260911_origin_gradients"
INDEX_SHA = "eea694ef27ccc188f0c2aec37996caf58a2ec41e30138233aa1878a39af89818"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    index = SOURCE / "artifact_manifest.json"
    if digest(index) != INDEX_SHA:
        raise ValueError("Frozen diagnostic index differs")
    manifest = json.loads(index.read_text())["files"]
    for name in (
        "gradients.csv",
        "parameter_layout.json",
        "prefix_342.npz",
        "prefix_432.npz",
        "prefix_612.npz",
    ):
        if digest(SOURCE / name) != manifest[name]["sha256"]:
            raise ValueError("Frozen diagnostic data differ")
    with (SOURCE / "gradients.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    layout = json.loads((SOURCE / "parameter_layout.json").read_text())
    checked, maximum = 0, 0.0
    for h in (342, 432, 612):
        with np.load(SOURCE / f"prefix_{h}.npz", allow_pickle=False) as data:
            for key, gradients in zip(data["keys"], data["gradients"]):
                strategy, seed, epoch = map(int, key)
                for group in ("all", "gates", "output"):
                    selected = [
                        i
                        for v in layout
                        if group == "all" or v["name"].startswith(group + ".")
                        for i in range(v["start"], v["stop"])
                    ]
                    a, b = [gradients[j, selected].tolist() for j in (0, 1)]
                    total = [(x + y) / 2 for x, y in zip(a, b)]
                    na, nb, nt = [
                        math.sqrt(math.fsum(x * x for x in v)) for v in (a, b, total)
                    ]
                    dot = math.fsum(x * y for x, y in zip(a, b))
                    expected = dict(
                        anchor_norm=na,
                        paired_norm=nb,
                        total_norm=nt,
                        dot=dot,
                        norm_ratio=math.nan if na <= 1e-12 else nb / na,
                        cosine=math.nan if min(na, nb) <= 1e-12 else dot / (na * nb),
                        anchor_directional=math.nan
                        if nt <= 1e-12
                        else -math.fsum(x * y for x, y in zip(a, total)) / nt,
                        paired_directional=math.nan
                        if nt <= 1e-12
                        else -math.fsum(x * y for x, y in zip(b, total)) / nt,
                    )
                    matches = [
                        r
                        for r in rows
                        if int(r["fit_days"]) == h
                        and r["strategy"] == ("IN", "OOF")[strategy]
                        and int(r["seed"]) == seed
                        and int(r["epoch"]) == epoch
                        and r["parameter_group"] == group
                    ]
                    if len(matches) != 1:
                        raise ValueError("Missing or duplicated geometry row")
                    for key, expected_value in expected.items():
                        actual = float(matches[0][key]) if matches[0][key] else math.nan
                        if math.isnan(expected_value):
                            if not math.isnan(actual):
                                raise ArithmeticError(
                                    "Zero-gradient missing value differs"
                                )
                        else:
                            if not math.isfinite(actual) or not math.isclose(
                                actual, expected_value, rel_tol=1e-12, abs_tol=1e-12
                            ):
                                raise ArithmeticError(
                                    f"Scalar geometry differs: {h}, {strategy}, {seed}, {epoch}, {group}, {key}"
                                )
                            maximum = max(maximum, abs(actual - expected_value))
                        checked += 1
    if len(rows) != 270 or checked != 2160:
        raise ValueError("Incomplete scalar check")
    return dict(
        scalar_geometry_passed=True,
        rows=270,
        values_checked=checked,
        max_absolute_difference=maximum,
        source_index_sha256=INDEX_SHA,
        checker_sha256=digest(Path(__file__).resolve()),
        method="python math.fsum and math.sqrt; no matrix products",
        warning_trigger_cause="not determined",
        new_network_calls=0,
        new_updates=0,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    result = verify()
    if args.record:
        with (SOURCE.parent / "scalar_geometry_check.json").open("x") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps(result, indent=2, allow_nan=False))
