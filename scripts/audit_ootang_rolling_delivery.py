"""Read-only artifact hashes, Git inclusion and local-link delivery audit."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path("results/ootang_rolling_v3/20260913")
DOCUMENTS = [
    "AGENTS.md",
    "docs/README.md",
    "docs/progress.md",
    "docs/ootang_rolling_probability_brief_2026-09-13.md",
    "docs/ootang_rolling_probability_implementation.v3.0.md",
    "docs/ootang_rolling_route_review_2026-09-13.md",
    "docs/ootang_rolling_consolidation.v2_2026-09-13.md",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main(args):
    head = git("rev-parse", "HEAD")
    tree = {}
    for line in git("ls-tree", "-r", head).splitlines():
        metadata, name = line.split("\t", 1)
        tree[name] = metadata.split()[2]
    checked = {}

    def verify_file(path, expected=None):
        name = str(path.relative_to(ROOT))
        sha = digest(path)
        if expected is not None and sha != expected:
            raise ValueError("Changed frozen file: " + name)
        if name not in tree:
            raise ValueError("File not in committed tree: " + name)
        contents = path.read_bytes()
        blob = hashlib.sha1(
            b"blob " + str(len(contents)).encode() + b"\0" + contents
        ).hexdigest()
        if tree[name] != blob:
            raise ValueError("Working file differs from committed bytes: " + name)
        checked[name] = sha

    manifests, members = {}, 0
    # Snapshot copies of upstream manifests are bytes in these manifests;
    # their relative paths must not be reinterpreted under the snapshot folder.
    for manifest in sorted((ROOT / RESULTS).glob("*/artifact_manifest.json")):
        verify_file(manifest)
        files = json.loads(manifest.read_text())["files"]
        for name, expected in files.items():
            verify_file(manifest.parent / name, expected)
        manifests[str(manifest.relative_to(ROOT))] = len(files)
        members += len(files)
    config = ROOT / "config/ootang_rolling_consolidation.v2.20260913.json"
    verify_file(config)
    cfg = json.loads(config.read_text())
    verify_file(ROOT / cfg["data"], cfg["data_sha256"])
    for entry in cfg["entries"]:
        verify_file(ROOT / entry["verification"], entry["verification_sha256"])
        if not json.loads((ROOT / entry["verification"]).read_text())["passed"]:
            raise ValueError("Failed model verification")
    links = []
    for name in DOCUMENTS:
        path = ROOT / name
        verify_file(path)
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            target = target.strip("<>").split("#")[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                raise ValueError("Missing local link: " + name + " -> " + target)
            if resolved.is_file():
                verify_file(resolved)
            elif not any(
                p.startswith(str(resolved.relative_to(ROOT)) + "/") for p in tree
            ):
                raise ValueError("Linked directory has no committed content")
            links.append(dict(document=name, target=target))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(__file__, out / Path(__file__).name)
    result = dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        evidence_commit=head,
        manifests=manifests,
        manifest_member_count=members,
        candidate_stage_rows=len(cfg["entries"]),
        files_sha256=checked,
        local_links=links,
        new_training=0,
        new_physics=0,
        checks_do_not_establish_model_effectiveness=True,
    )
    (out / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in ("files_sha256", "local_links", "manifests")
            }
            | dict(manifests=len(manifests), files=len(checked), links=len(links)),
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    main(parser.parse_args())
