"""Run the landslide-warning workflow from one auditable entry point."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "figures" / "pipeline" / "latest_run.json"


@dataclass(frozen=True)
class Stage:
    name: str
    script: str
    description: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()


STAGES = (
    Stage(
        "features",
        "code/features.py",
        "生成统一特征表和切线角参数",
        inputs=("data/monitoring_data.csv", "config/tangent_reference_stages.csv"),
        outputs=("data/features.csv", "figures/tangent_angle/uniform_rates.csv"),
    ),
    Stage(
        "onset",
        "code/onset_analysis.py",
        "生成未来 onset 标签和事件盘点",
        inputs=("data/monitoring_data.csv",),
        outputs=(
            "figures/warning_onset/onset_events.csv",
            "figures/warning_onset/onset_targets.csv",
            "figures/warning_onset/onset_inventory.csv",
            "figures/thresholds/v0_thresholds.csv",
        ),
    ),
    Stage(
        "shap",
        "code/shap_select.py",
        "训练解释模型并输出 SHAP 分析",
        inputs=("data/monitoring_data.csv",),
        outputs=(
            "figures/shap/shap_reg_summary.png",
            "figures/shap/shap_cls_summary.png",
            "figures/shap/shap_reg_importance.csv",
            "figures/shap/shap_cls_importance.csv",
            "figures/shap/shap_model_metrics.csv",
            "figures/shap/shap_binary_cv_metrics.csv",
            "figures/thresholds/v0_thresholds.csv",
        ),
    ),
    Stage(
        "convlstm",
        "code/convlstm.py",
        "训练 ConvLSTM 位移区间预测模型",
        inputs=("data/features.csv", "data/station_coords.csv"),
        outputs=(
            "models/convlstm.pt",
            "figures/convlstm/forecast_interval.png",
            "figures/convlstm/forecast_metrics.csv",
            "figures/convlstm/forecast_period_metrics.csv",
            "figures/convlstm/forecast_calibration_metrics.csv",
            "figures/convlstm/forecast_bootstrap_ci.csv",
        ),
    ),
    Stage(
        "ngboost",
        "code/ngboost_warn.py",
        "训练 NGBoost 预警概率模型",
        inputs=("data/features.csv", "data/monitoring_data.csv"),
        outputs=(
            "models/ngboost.pkl",
            "figures/ngboost/confusion_matrix.png",
            "figures/ngboost/warning_metrics.csv",
            "figures/ngboost/warning_probabilities.csv",
            "figures/thresholds/v0_thresholds.csv",
        ),
    ),
    Stage(
        "fusion",
        "code/warning_fusion.py",
        "融合 V0、切线角和概率旁证",
        inputs=(
            "data/features.csv",
            "data/monitoring_data.csv",
            "figures/ngboost/warning_probabilities.csv",
        ),
        outputs=("figures/warning_fusion/warning_fusion.csv",),
    ),
    Stage(
        "sensitivity",
        "code/sensitivity_analysis.py",
        "执行预设参数敏感性分析",
        inputs=("data/monitoring_data.csv",),
        outputs=(
            "figures/sensitivity/v0_sensitivity.csv",
            "figures/sensitivity/v0_parameters.csv",
            "figures/sensitivity/tangent_sensitivity.csv",
            "figures/sensitivity/tangent_parameters.csv",
        ),
    ),
    Stage(
        "tangent-review",
        "code/tangent_stage_review.py",
        "生成等速阶段专家复核材料",
        inputs=("data/monitoring_data.csv",),
        outputs=(
            "figures/tangent_angle/review/MJ9_stage_review.png",
            "figures/tangent_angle/review/MJ1_stage_review.png",
            "figures/tangent_angle/review/MJ3_stage_review.png",
            "figures/tangent_angle/review/candidate_stage_comparison.csv",
        ),
    ),
)
STAGE_BY_NAME = {stage.name: stage for stage in STAGES}
Runner = Callable[..., subprocess.CompletedProcess]


class PipelineContractError(RuntimeError):
    def __init__(self, stage: str, kind: str, paths: Sequence[str]):
        self.stage = stage
        self.kind = kind
        self.paths = list(paths)
        super().__init__(f"{stage}: {kind}: {', '.join(self.paths)}")


def current_git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def source_fingerprint() -> str:
    paths = [ROOT / "main.py", ROOT / "pyproject.toml", ROOT / "uv.lock"]
    paths.extend(sorted((ROOT / "code").glob("*.py")))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def file_fingerprint(path: Path, root: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path.relative_to(root)),
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def select_stages(
    selected: Sequence[str] | None = None,
    skipped: Sequence[str] | None = None,
) -> list[Stage]:
    """Return requested stages in the canonical workflow order."""
    selected_names = set(selected or STAGE_BY_NAME)
    skipped_names = set(skipped or ())
    return [
        stage
        for stage in STAGES
        if stage.name in selected_names and stage.name not in skipped_names
    ]


def run_pipeline(
    stages: Sequence[Stage],
    *,
    dry_run: bool = False,
    runner: Runner = subprocess.run,
    manifest_path: Path | None = None,
    root: Path = ROOT,
    verify_contracts: bool = True,
) -> dict | None:
    """Run each stage in an isolated Python process and stop on first failure."""
    if not stages:
        print("[pipeline] 没有需要执行的阶段")
        return None

    total_start = time.perf_counter()
    completed: list[tuple[str, float]] = []
    manifest = {
        "schema_version": 2,
        "status": "running",
        "started_at": timestamp(),
        "finished_at": None,
        "git_commit": current_git_commit(),
        "source_sha256": source_fingerprint(),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "total_elapsed_seconds": None,
        "failed_stage": None,
        "stages": [],
    }
    print(
        "[pipeline] 执行顺序: " + " -> ".join(stage.name for stage in stages),
        flush=True,
    )

    for index, stage in enumerate(stages, start=1):
        command = [sys.executable, str(root / stage.script)]
        print(
            f"\n[pipeline] [{index}/{len(stages)}] {stage.name}: {stage.description}",
            flush=True,
        )
        print("[pipeline] 命令: " + " ".join(command), flush=True)
        if dry_run:
            continue

        stage_start = time.perf_counter()
        stage_result = {
            "name": stage.name,
            "script": stage.script,
            "status": "running",
            "elapsed_seconds": None,
            "returncode": None,
            "contract_status": "pending" if verify_contracts else "not_checked",
            "inputs": list(stage.inputs),
            "outputs": [],
            "contract_error": None,
        }
        manifest["stages"].append(stage_result)
        input_paths = [root / path for path in stage.inputs]
        missing_inputs = [
            str(path.relative_to(root)) for path in input_paths if not path.is_file()
        ]
        if verify_contracts and missing_inputs:
            elapsed = time.perf_counter() - stage_start
            total_elapsed = time.perf_counter() - total_start
            stage_result.update(
                status="failed",
                elapsed_seconds=round(elapsed, 3),
                contract_status="failed",
                contract_error={"kind": "missing_inputs", "paths": missing_inputs},
            )
            manifest.update(
                status="failed",
                finished_at=timestamp(),
                total_elapsed_seconds=round(total_elapsed, 3),
                failed_stage=stage.name,
            )
            if manifest_path is not None:
                write_manifest(manifest_path, manifest)
            raise PipelineContractError(stage.name, "missing_inputs", missing_inputs)

        output_paths = [root / path for path in stage.outputs]
        output_mtimes = {
            path: path.stat().st_mtime_ns if path.is_file() else None
            for path in output_paths
        }
        try:
            runner(command, cwd=root, check=True)
        except subprocess.CalledProcessError as exc:
            elapsed = time.perf_counter() - stage_start
            total_elapsed = time.perf_counter() - total_start
            stage_result.update(
                status="failed",
                elapsed_seconds=round(elapsed, 3),
                returncode=exc.returncode,
                contract_status="not_checked",
            )
            manifest.update(
                status="failed",
                finished_at=timestamp(),
                total_elapsed_seconds=round(total_elapsed, 3),
                failed_stage=stage.name,
            )
            if manifest_path is not None:
                write_manifest(manifest_path, manifest)
            print(f"[pipeline] 失败: {stage.name} ({elapsed:.1f}s)", file=sys.stderr)
            print(
                f"[pipeline] 已完成 {len(completed)}/{len(stages)} 个阶段，"
                f"总耗时 {total_elapsed:.1f}s",
                file=sys.stderr,
            )
            raise
        elapsed = time.perf_counter() - stage_start
        if verify_contracts:
            missing_outputs = [
                str(path.relative_to(root))
                for path in output_paths
                if not path.is_file()
            ]
            unchanged_outputs = [
                str(path.relative_to(root))
                for path in output_paths
                if path.is_file()
                and output_mtimes[path] is not None
                and path.stat().st_mtime_ns <= output_mtimes[path]
            ]
            if missing_outputs or unchanged_outputs:
                kind = "missing_outputs" if missing_outputs else "unchanged_outputs"
                paths = missing_outputs or unchanged_outputs
                total_elapsed = time.perf_counter() - total_start
                stage_result.update(
                    status="failed",
                    elapsed_seconds=round(elapsed, 3),
                    returncode=0,
                    contract_status="failed",
                    contract_error={"kind": kind, "paths": paths},
                )
                manifest.update(
                    status="failed",
                    finished_at=timestamp(),
                    total_elapsed_seconds=round(total_elapsed, 3),
                    failed_stage=stage.name,
                )
                if manifest_path is not None:
                    write_manifest(manifest_path, manifest)
                raise PipelineContractError(stage.name, kind, paths)

            stage_result["outputs"] = [
                file_fingerprint(path, root) for path in output_paths
            ]
        stage_result.update(
            status="completed",
            elapsed_seconds=round(elapsed, 3),
            returncode=0,
            contract_status="passed" if verify_contracts else "not_checked",
        )
        completed.append((stage.name, elapsed))
        print(f"[pipeline] 完成: {stage.name} ({elapsed:.1f}s)")

    if dry_run:
        print("\n[pipeline] dry-run 完成，未执行任何脚本")
        return None

    total_elapsed = time.perf_counter() - total_start
    manifest.update(
        status="completed",
        finished_at=timestamp(),
        total_elapsed_seconds=round(total_elapsed, 3),
    )
    if manifest_path is not None:
        write_manifest(manifest_path, manifest)
        print(f"[pipeline] 运行清单: {manifest_path}")
    timing = ", ".join(f"{name}={elapsed:.1f}s" for name, elapsed in completed)
    print(f"\n[pipeline] 全部完成 ({total_elapsed:.1f}s): {timing}")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行滑坡预测与预警研究管线")
    parser.add_argument(
        "--stage",
        action="append",
        choices=STAGE_BY_NAME,
        help="只执行指定阶段，可重复使用；按标准流程顺序运行",
    )
    parser.add_argument(
        "--skip",
        action="append",
        choices=STAGE_BY_NAME,
        default=[],
        help="跳过指定阶段，可重复使用",
    )
    parser.add_argument("--dry-run", action="store_true", help="只显示执行顺序和命令")
    parser.add_argument("--list", action="store_true", help="列出可用阶段后退出")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="运行清单路径，默认为 figures/pipeline/latest_run.json",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = subprocess.run,
    verify_contracts: bool = True,
) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        for stage in STAGES:
            print(f"{stage.name:16s} {stage.description}")
        return 0

    stages = select_stages(args.stage, args.skip)
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    try:
        run_pipeline(
            stages,
            dry_run=args.dry_run,
            runner=runner,
            manifest_path=manifest_path,
            verify_contracts=verify_contracts,
        )
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1
    except PipelineContractError as exc:
        print(f"[pipeline] 契约失败: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
