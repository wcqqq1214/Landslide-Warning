"""Read-only source checks and exclusive diagnostic outputs; no model imports."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .core import POINTS

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/ootang_bplus_error_structure.v1_5.json"
CONFIG_SHA = "ee6d8e7057731456192961637af2f5daf56a061d4e84083bed34ca212af0bd96"
PROTOCOL_SHA = "d5557a4ab2c2e1176257c9c224d00381fc9c3cd79def93d9da26de5df85ff69f"


def sha(path):
    with Path(path).open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def array_sha(values):
    return hashlib.sha256(
        np.ascontiguousarray(values, dtype=np.float64).tobytes()
    ).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def seal(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def load_observations(path, days):
    """Parsing is limited to the requested prefix, even if later data are invalid."""
    if not isinstance(days, int) or not 31 <= days <= 792:
        raise ValueError("Only prefixes within the first 792 days are permitted")
    frame = pd.read_csv(path, nrows=days)
    dates = pd.DatetimeIndex(pd.to_datetime(frame.Date, errors="raise"))
    labels = frame[[p + "/mm" for p in POINTS]].to_numpy(dtype=float)
    forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(dtype=float)
    if not dates.equals(pd.date_range("2016-07-01", periods=days)):
        raise ValueError("Input dates must be the exact consecutive prefix")
    if not np.isfinite(labels).all() or not np.isfinite(forcing).all():
        raise ValueError("Nonfinite input; no filling or clipping")
    return dates.strftime("%Y-%m-%d").to_numpy(), forcing, labels


def trajectory_name(n, recipe):
    if n == 252:
        return f"inner_432_252_{recipe}.npz"
    if n == 342:
        return f"inner_612_342_{recipe}.npz"
    return f"outer_{n}_{recipe}.npz"


def check_hashes(mapping, base=ROOT):
    for name, digest in mapping.items():
        if sha(base / name) != digest:
            raise ValueError(f"Source changed: {name}")


def check_index(out):
    index = read_json(out / "artifact_manifest.json")["files"]
    actual = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    if actual != set(index) | {"artifact_manifest.json"}:
        raise ValueError("Artifact inventory differs from the sealed index")
    check_hashes({p: info["sha256"] for p, info in index.items()}, out)
    for name, info in index.items():
        if (out / name).stat().st_size != info["bytes"]:
            raise ValueError(f"Artifact size changed: {name}")


def index_artifacts(out):
    seal(
        out / "artifact_manifest.json",
        dict(
            files={
                str(p.relative_to(out)): dict(sha256=sha(p), bytes=p.stat().st_size)
                for p in sorted(out.rglob("*"))
                if p.is_file()
            }
        ),
    )


def source_guard(spec):
    source = ROOT / spec["source_run"]
    check_index(source)
    protected = read_json(source / "protected_before.json")
    scientific = read_json(source / "manifest.json")["sources"]
    protected.update(scientific)
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in source.rglob("*") if p.is_file()}
    )
    protected[str(CONFIG.relative_to(ROOT))] = CONFIG_SHA
    protected[spec["protocol"]] = PROTOCOL_SHA
    check_hashes(protected)
    return protected
