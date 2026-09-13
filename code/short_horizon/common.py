"""Run boundaries, immutable sources, and append-only execution receipts."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CALLS = {"reference_forward": 0, "day_forward": 0, "day_backward": 0}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_sha(array):
    a = np.ascontiguousarray(array)
    return hashlib.sha256(str((a.dtype, a.shape)).encode() + a.tobytes()).hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    temporary.replace(path)


def load_spec(path):
    spec = json.loads(Path(path).read_text())
    for name, expected in spec["source_sha256"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("Frozen source changed: " + name)
    if spec["horizons"] != list(range(1, 8)):
        raise ValueError("Only the frozen seven endpoints are supported")
    return spec


def check_deadline(spec):
    if datetime.now(timezone.utc) >= datetime.fromisoformat(spec["deadline_utc"]):
        raise TimeoutError("Frozen total execution deadline reached")


class Recorder:
    def __init__(self, directory, spec, phase):
        self.root, self.spec, self.phase = Path(directory), spec, phase
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "events.jsonl"
        self.start = time.monotonic()
        self.event(
            "started",
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
        )

    def event(self, kind, **data):
        entry = dict(
            time_utc=now(),
            phase=self.phase,
            event=kind,
            elapsed_seconds=time.monotonic() - self.start,
            **data,
        )
        with self.path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")

    def guard(self):
        check_deadline(self.spec)
