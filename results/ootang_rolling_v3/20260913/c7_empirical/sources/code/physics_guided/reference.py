"""Read the frozen delivery without importing any historic calibration code."""

import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_PREFIX = "section2d_v4/work/delivery_final/outang_repro/"
MODEL = "section2d_v4/"
ARCHIVE_SHA = "57f3e7c68f004a47875c59ee93d38c384900b5263f111341fdd4aee36fdcc966"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )


def prepare(directory):
    """Extract only final dependencies, preserve paths and record the sole loader patch."""
    directory = Path(directory)
    archive = ROOT / "section2d_v4.zip"
    if sha(archive) != ARCHIVE_SHA:
        raise ValueError(
            "Unexpected source archive; requires a separately reviewed version"
        )
    files = ["work/monitoring.csv"]
    files += [
        f"results/geometry_{b}_interpretation.csv" for b in ["ground", "O3", "O2", "O1"]
    ]
    files += [
        MODEL + s
        for s in [
            "physical_model.py",
            "physical_solver.c",
            "geometry.py",
            "calibrate_boundary.py",
            "predict.py",
            "results/calibrated.json",
            "results/calibration_lock.json",
            "results/protocol.json",
            "results/metrics.json",
            "results/curves.npz",
            "results/geometry_matrices.npz",
            "results/reservoir_force_lookup_final.npz",
            "output/future_prediction.csv",
        ]
    ]
    hashes = {}
    with ZipFile(archive) as z:
        for name in files:
            raw = z.read(ARCHIVE_PREFIX + name)
            p = directory / name
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.read_bytes() != raw:
                raise ValueError(f"Frozen extracted source changed: {name}")
            p.write_bytes(raw)
            hashes[name] = sha(p)
    model = directory / MODEL
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    library = model / ("physical_solver" + suffix)
    cmd = [
        "cc",
        "-O3",
        "-fPIC",
        "-shared",
        str(model / "physical_solver.c"),
        "-o",
        str(library),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    source = (model / "physical_model.py").read_text()
    source = source.replace(
        "ROOT/'physical_solver.dll'", f"ROOT/'physical_solver{suffix}'"
    )
    # A unique package avoids the generic `geometry` name leaking across imports.
    source = source.replace("from geometry import", "from .geometry import")
    adapted = model / "physical_model_posix.py"
    adapted.write_text(source)
    provenance = dict(
        archive_sha256=sha(archive),
        source_hashes=hashes,
        adapted_python_sha256=sha(adapted),
        library_sha256=sha(library),
        compiler=subprocess.check_output(["cc", "--version"], text=True),
        command=cmd,
        platform=platform.platform(),
        adaptations=["dynamic library suffix", "package-relative geometry import"],
    )
    save_json(directory / "provenance.json", provenance)
    return load(directory)


def load(directory):
    directory = Path(directory).resolve()
    model = directory / MODEL
    name = "_ootang_frozen_" + hashlib.sha256(str(model).encode()).hexdigest()[:12]
    if name + ".physical_model_posix" in sys.modules:
        return sys.modules[name + ".physical_model_posix"]
    import types

    pkg = types.ModuleType(name)
    pkg.__path__ = [str(model)]
    sys.modules[name] = pkg
    spec = importlib.util.spec_from_file_location(
        name + ".physical_model_posix", model / "physical_model_posix.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.LIB.moisture.restype = None
    return module


def frozen_theta(ref):
    cfg = json.loads((ref.ROOT / "results/calibrated.json").read_text())
    return np.array(cfg["theta"], dtype=np.float64), np.array(
        cfg["y0"], dtype=np.float64
    )
