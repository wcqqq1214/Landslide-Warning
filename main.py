"""Run the landslide-warning workflow from one auditable entry point."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "figures" / "pipeline" / "latest_run.json"
WARNING_PIPELINE_SCOPE = "research_legacy_and_operational_draft_only"
FORMAL_WARNING_ENTRY = "code/warning/formal_warning.py"
CONVLSTM_PROTOCOL_FILE = "config/ootang_convlstm_elevation_diagnostics.v1.json"
CONVLSTM_DIAGNOSTIC_ROOT = json.loads(
    (ROOT / CONVLSTM_PROTOCOL_FILE).read_text(encoding="utf-8")
)["output_contract"]["run_root"]


@dataclass(frozen=True)
class Stage:
    name: str
    script: str
    description: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    warning_artifact_scope: str = "research_support"
    formal_warning_output: bool = False
    enabled_by_default: bool = False


STAGES = (
    Stage(
        "features",
        "code/features/build_features.py",
        "生成统一特征表、时间感知运动学长表和切线角参数",
        inputs=("data/monitoring_data.csv",),
        outputs=(
            "data/features.csv",
            "data/ootang_kinematics_long.csv",
            "data/ootang_kinematics_summary.csv",
            "figures/tangent_angle/uniform_rates.csv",
        ),
        enabled_by_default=True,
    ),
    Stage(
        "onset",
        "code/warning/onset_analysis.py",
        "生成遗留 V0 标签的未来 onset 盘点（历史/探索）",
        inputs=("data/monitoring_data.csv",),
        outputs=(
            "figures/warning_onset/onset_events.csv",
            "figures/warning_onset/onset_targets.csv",
            "figures/warning_onset/onset_inventory.csv",
            "figures/thresholds/v0_thresholds.csv",
            "figures/warning_onset/legacy_warning_manifest.json",
        ),
        warning_artifact_scope="legacy_exploratory",
    ),
    Stage(
        "shap",
        "code/explainability/shap_select.py",
        "训练遗留 V0 标签解释模型并输出 SHAP 分析（历史/探索）",
        inputs=("data/monitoring_data.csv",),
        outputs=(
            "figures/shap/shap_reg_summary.png",
            "figures/shap/shap_cls_summary.png",
            "figures/shap/shap_reg_importance.csv",
            "figures/shap/shap_cls_importance.csv",
            "figures/shap/shap_model_metrics.csv",
            "figures/shap/shap_binary_cv_metrics.csv",
            "figures/shap/shap_provenance.json",
            "figures/thresholds/v0_thresholds.csv",
            "figures/shap/legacy_warning_manifest.json",
        ),
        warning_artifact_scope="legacy_exploratory",
    ),
    Stage(
        "shap-stability",
        "code/explainability/shap_stability.py",
        "执行遗留标签的跨折 SHAP 稳定性和特征组消融（历史/探索）",
        inputs=("data/monitoring_data.csv", "docs/shap_stability_protocol.md"),
        outputs=(
            "figures/shap/stability/cross_fold_protocol.csv",
            "figures/shap/stability/cross_fold_feature_importance.csv",
            "figures/shap/stability/cross_fold_feature_stability.csv",
            "figures/shap/stability/cross_fold_rank_stability.csv",
            "figures/shap/stability/cross_fold_station_feature_importance.csv",
            "figures/shap/stability/cross_fold_station_feature_stability.csv",
            "figures/shap/stability/cross_fold_group_importance.csv",
            "figures/shap/stability/group_ablation_fold_metrics.csv",
            "figures/shap/stability/group_ablation_summary.csv",
            "figures/shap/stability/shap_group_stability.png",
            "figures/shap/stability/group_ablation.png",
            "figures/shap/stability/legacy_warning_manifest.json",
        ),
        warning_artifact_scope="legacy_exploratory",
    ),
    Stage(
        "convlstm",
        "code/convlstm/model.py",
        "训练 ConvLSTM 位移区间预测模型",
        inputs=("data/features.csv", "data/station_coords.csv"),
        outputs=(
            "models/convlstm.pt",
            "figures/convlstm/forecast_all_stations.png",
            "figures/convlstm/forecast_predictions.csv",
            "figures/convlstm/forecast_metrics.csv",
            "figures/convlstm/forecast_period_metrics.csv",
            "figures/convlstm/forecast_calibration_metrics.csv",
            "figures/convlstm/forecast_bootstrap_ci.csv",
            "figures/convlstm/forecast_run_manifest.json",
        ),
        enabled_by_default=True,
    ),
    Stage(
        "convlstm-rolling",
        "code/convlstm/rolling_validation.py",
        "执行 ConvLSTM 扩展窗口滚动时间验证",
        inputs=(
            "data/features.csv",
            "data/station_coords.csv",
            CONVLSTM_PROTOCOL_FILE,
        ),
        outputs=(
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/rolling_seed0/rolling_validation_folds.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/rolling_seed0/rolling_validation_metrics.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/rolling_seed0/rolling_validation_predictions.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/rolling_seed0/manifest.json",
        ),
    ),
    Stage(
        "convlstm-seeds",
        "code/convlstm/seed_stability.py",
        "执行 ConvLSTM 固定协议多随机种子诊断",
        inputs=(
            "data/features.csv",
            "data/station_coords.csv",
            CONVLSTM_PROTOCOL_FILE,
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/rolling_seed0/rolling_validation_folds.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/rolling_seed0/rolling_validation_metrics.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/rolling_seed0/rolling_validation_predictions.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/rolling_seed0/manifest.json",
        ),
        outputs=(
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/seed_stability_runs.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/seed_stability_metrics.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/seed_stability_summary.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/seed_stability_training.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/seed_stability_predictions.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/manifest.json",
        ),
    ),
    Stage(
        "convlstm-inner-validation",
        "code/convlstm/inner_validation.py",
        "执行 ConvLSTM 内层时间验证和早停诊断",
        inputs=(
            "data/features.csv",
            "data/station_coords.csv",
            CONVLSTM_PROTOCOL_FILE,
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/seed_stability_metrics.csv",
        ),
        outputs=(
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/inner_validation_v1/inner_validation_runs.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/inner_validation_v1/inner_validation_selection_history.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/inner_validation_v1/inner_validation_refit_history.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/inner_validation_v1/inner_validation_metrics.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/inner_validation_v1/inner_validation_summary.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/inner_validation_v1/inner_validation_predictions.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/inner_validation_v1/inner_validation_comparison.csv",
        ),
        enabled_by_default=False,
    ),
    Stage(
        "convlstm-capacity",
        "code/convlstm/capacity_sensitivity.py",
        "执行 ConvLSTM 有限容量与正则化敏感性诊断",
        inputs=(
            "data/features.csv",
            "data/station_coords.csv",
            CONVLSTM_PROTOCOL_FILE,
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/inner_validation_v1/inner_validation_runs.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/inner_validation_v1/inner_validation_metrics.csv",
        ),
        outputs=(
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/capacity_sensitivity_v1/capacity_candidates.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/capacity_sensitivity_v1/capacity_selection_summary.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/capacity_sensitivity_v1/capacity_selection_history.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/capacity_sensitivity_v1/capacity_selected_runs.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/capacity_sensitivity_v1/capacity_selected_refit_history.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/capacity_sensitivity_v1/capacity_selected_metrics.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/capacity_sensitivity_v1/capacity_selected_summary.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/capacity_sensitivity_v1/capacity_selected_predictions.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/capacity_sensitivity_v1/capacity_selected_comparison.csv",
        ),
        enabled_by_default=False,
    ),
    Stage(
        "ootang-operational",
        "code/warning/operational_run.py",
        "运行藕塘四指标实施版并输出非正式逐点/滑坡体时间线",
        inputs=(
            "data/ootang_kinematics_long.csv",
            "figures/convlstm/forecast_predictions.csv",
            "figures/convlstm/forecast_run_manifest.json",
            "config/ootang_warning_protocol.v1.draft.json",
            "config/ootang_operational_run.v1.draft.json",
        ),
        outputs=(
            "figures/warning_operational_draft/ootang_operational_thresholds.csv",
            "figures/warning_operational_draft/ootang_operational_station_timeline.csv",
            "figures/warning_operational_draft/ootang_operational_site_timeline.csv",
            "figures/warning_operational_draft/ootang_operational_run_manifest.json",
        ),
        warning_artifact_scope="operational_draft",
    ),
    Stage(
        "ootang-operational-v2",
        "code/warning/operational_run_v2.py",
        "运行藕塘 v2 空间证据族实施版并输出非正式逐点/滑坡体时间线",
        inputs=(
            "data/ootang_kinematics_long.csv",
            "figures/convlstm/forecast_predictions.csv",
            "figures/convlstm/forecast_run_manifest.json",
            "config/ootang_warning_protocol.v1.draft.json",
            "config/ootang_operational_run.v2.draft.json",
        ),
        outputs=(
            "figures/warning_operational_draft_v2/ootang_operational_thresholds.csv",
            "figures/warning_operational_draft_v2/ootang_operational_station_timeline.csv",
            "figures/warning_operational_draft_v2/ootang_operational_site_timeline.csv",
            "figures/warning_operational_draft_v2/ootang_operational_run_manifest.json",
        ),
        warning_artifact_scope="operational_draft",
    ),
    Stage(
        "ootang-operational-v3",
        "code/warning/operational_run_v3.py",
        "运行藕塘 v3 双轴空间实施版并输出非正式逐点/滑坡体时间线",
        inputs=(
            "data/ootang_kinematics_long.csv",
            "figures/convlstm/forecast_predictions.csv",
            "figures/convlstm/forecast_run_manifest.json",
            "config/ootang_warning_protocol.v1.draft.json",
            "config/ootang_operational_run.v3.draft.json",
            "config/ootang_operational_v3_typical_days.v1.json",
            "config/ootang_operational_v3_station_diagnostic.v1.json",
        ),
        outputs=(
            "figures/warning_operational_draft_v3/ootang_operational_thresholds.csv",
            "figures/warning_operational_draft_v3/ootang_operational_station_timeline.csv",
            "figures/warning_operational_draft_v3/ootang_operational_site_timeline.csv",
            "figures/warning_operational_draft_v3/ootang_operational_run_manifest.json",
            "figures/warning_operational_draft_v3/ootang_v3_typical_days.svg",
            "figures/warning_operational_draft_v3/ootang_v3_typical_days.pdf",
            "figures/warning_operational_draft_v3/ootang_v3_typical_days.png",
            "figures/warning_operational_draft_v3/ootang_v3_typical_days_manifest.json",
            "figures/warning_operational_draft_v3/ootang_v3_full_warning_timeline.svg",
            "figures/warning_operational_draft_v3/ootang_v3_full_warning_timeline.pdf",
            "figures/warning_operational_draft_v3/ootang_v3_full_warning_timeline.png",
            "figures/warning_operational_draft_v3/ootang_v3_full_warning_timeline_manifest.json",
            "figures/warning_operational_draft_v3/ootang_v3_all_station_combined_diagnostic.svg",
            "figures/warning_operational_draft_v3/ootang_v3_all_station_combined_diagnostic.pdf",
            "figures/warning_operational_draft_v3/ootang_v3_all_station_combined_diagnostic.png",
            "figures/warning_operational_draft_v3/ootang_v3_all_station_combined_diagnostic_manifest.json",
        ),
        warning_artifact_scope="operational_draft",
        enabled_by_default=True,
    ),
    Stage(
        "ngboost",
        "code/warning/ngboost_warn.py",
        "训练遗留 V0 当日状态 NGBoost（历史/探索）",
        inputs=("data/features.csv", "data/monitoring_data.csv"),
        outputs=(
            "models/ngboost.pkl",
            "figures/ngboost/confusion_matrix.png",
            "figures/ngboost/warning_metrics.csv",
            "figures/ngboost/warning_probabilities.csv",
            "figures/thresholds/v0_thresholds.csv",
            "figures/ngboost/legacy_warning_manifest.json",
            "models/ngboost_legacy_warning_manifest.json",
        ),
        warning_artifact_scope="legacy_exploratory",
    ),
    Stage(
        "fusion",
        "code/warning/warning_fusion.py",
        "复核旧 30 日 V0 主副融合（历史/探索）",
        inputs=(
            "data/features.csv",
            "data/monitoring_data.csv",
            "figures/ngboost/warning_probabilities.csv",
        ),
        outputs=(
            "figures/warning_fusion/warning_fusion.csv",
            "figures/warning_fusion/legacy_warning_manifest.json",
        ),
        warning_artifact_scope="legacy_exploratory",
    ),
    Stage(
        "sensitivity",
        "code/warning/sensitivity_analysis.py",
        "执行遗留 V0/切线角参数敏感性分析（历史/探索）",
        inputs=("data/monitoring_data.csv",),
        outputs=(
            "figures/sensitivity/v0_sensitivity.csv",
            "figures/sensitivity/v0_parameters.csv",
            "figures/sensitivity/tangent_sensitivity.csv",
            "figures/sensitivity/tangent_parameters.csv",
            "figures/sensitivity/legacy_warning_manifest.json",
        ),
        warning_artifact_scope="legacy_exploratory",
    ),
    Stage(
        "tangent-review",
        "code/features/tangent_stage_review.py",
        "生成遗留融合影响的等速阶段专家复核材料（历史/探索）",
        inputs=("data/monitoring_data.csv",),
        outputs=(
            "figures/tangent_angle/review/MJ9_stage_review.png",
            "figures/tangent_angle/review/MJ1_stage_review.png",
            "figures/tangent_angle/review/MJ3_stage_review.png",
            "figures/tangent_angle/review/ATU1_stage_review.png",
            "figures/tangent_angle/review/ATU2_stage_review.png",
            "figures/tangent_angle/review/ATU3_stage_review.png",
            "figures/tangent_angle/review/ATU4_stage_review.png",
            "figures/tangent_angle/review/ATU5_stage_review.png",
            "figures/tangent_angle/review/candidate_stage_comparison.csv",
            "figures/tangent_angle/review/legacy_warning_manifest.json",
        ),
        warning_artifact_scope="legacy_exploratory",
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
    paths.extend(sorted((ROOT / "code").rglob("*.py")))
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
    selected_names = (
        {stage.name for stage in STAGES if stage.enabled_by_default}
        if selected is None
        else set(selected)
    )
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
        "warning_pipeline_scope": WARNING_PIPELINE_SCOPE,
        "formal_warning_output": False,
        "formal_warning_entry": FORMAL_WARNING_ENTRY,
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
            "warning_artifact_scope": stage.warning_artifact_scope,
            "formal_warning_output": stage.formal_warning_output,
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
            selection_scope = (
                "default" if stage.enabled_by_default else "explicit-only"
            )
            print(
                f"{stage.name:24s} [{stage.warning_artifact_scope}; "
                f"{selection_scope}] "
                f"{stage.description}"
            )
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
