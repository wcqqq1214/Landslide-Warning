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
WARNING_PIPELINE_SCOPE = "research_and_operational_draft_only"
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
        "ngboost-shap",
        "code/explainability/ngboost_shap.py",
        "执行独立 NGBoost 回归的候选模型依赖 SHAP 分析",
        inputs=("data/monitoring_data.csv",),
        outputs=(
            "figures/shap/ngboost_regression_shap.png",
            "figures/shap/ngboost_regression_shap_importance.csv",
            "figures/shap/ngboost_regression_metrics.csv",
            "figures/shap/ngboost_regression_shap_provenance.json",
        ),
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
        "ootang-prequential-monitor",
        "code/monitoring/ootang_prequential_monitor.py",
        "运行藕塘五种子 OOF 预测的全自动 prequential 校准、漂移与退避回放（非正式）",
        inputs=(
            "config/ootang_prequential_monitor.v1.json",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/seed_stability_predictions.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/manifest.json",
        ),
        outputs=(
            "figures/prequential_anomaly_ootang_v1/station_timeline.csv",
            "figures/prequential_anomaly_ootang_v1/site_timeline.csv",
            "figures/prequential_anomaly_ootang_v1/prequential_metrics.csv",
            "figures/prequential_anomaly_ootang_v1/manifest.json",
        ),
        warning_artifact_scope="retrospective_prequential_monitoring_research",
        enabled_by_default=False,
    ),
    Stage(
        "ootang-operational-v4",
        "code/warning/operational_run_v4.py",
        "运行藕塘 v4 严格逐点加速度四指标/双轴空间实施版（非正式）",
        inputs=(
            "data/ootang_kinematics_long.csv",
            "figures/convlstm/forecast_predictions.csv",
            "figures/convlstm/forecast_run_manifest.json",
            "config/ootang_warning_protocol.v1.draft.json",
            "config/ootang_warning_protocol.v2.draft.json",
            "config/ootang_operational_run.v4.draft.json",
        ),
        outputs=(
            "figures/warning_operational_draft_v4/ootang_operational_thresholds.csv",
            "figures/warning_operational_draft_v4/ootang_operational_station_timeline.csv",
            "figures/warning_operational_draft_v4/ootang_operational_site_timeline.csv",
            "figures/warning_operational_draft_v4/ootang_operational_run_manifest.json",
            "figures/warning_operational_draft_v4/ootang_v4_typical_days.svg",
            "figures/warning_operational_draft_v4/ootang_v4_typical_days.png",
            "figures/warning_operational_draft_v4/ootang_v4_typical_days_manifest.json",
            "figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.svg",
            "figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline.png",
            "figures/warning_operational_draft_v4/ootang_v4_full_warning_timeline_manifest.json",
            "figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic.svg",
            "figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic.png",
            "figures/warning_operational_draft_v4/ootang_v4_all_station_combined_diagnostic_manifest.json",
        ),
        warning_artifact_scope="operational_draft",
        enabled_by_default=True,
    ),
    Stage(
        "ootang-ngboost-interval-proxy-pilot",
        "code/warning/ootang_ngboost_interval_proxy_pilot.py",
        "运行藕塘 NGBoost 下一日区间风险代理五分类试验（显式、非正式）",
        inputs=(
            "config/ootang_ngboost_interval_proxy_pilot.v1.json",
            "figures/convlstm/forecast_predictions.csv",
            "figures/convlstm/forecast_run_manifest.json",
            "data/ootang_kinematics_long.csv",
            "figures/warning_operational_draft_v4/ootang_operational_thresholds.csv",
            "figures/warning_operational_draft_v4/ootang_operational_run_manifest.json",
        ),
        outputs=(
            "models/ootang_ngboost_interval_proxy_pilot_v1.pkl",
            "figures/ngboost_interval_proxy_pilot_ootang_v1/predictions.csv",
            "figures/ngboost_interval_proxy_pilot_ootang_v1/metrics.csv",
            "figures/ngboost_interval_proxy_pilot_ootang_v1/confusion_matrices.csv",
            "figures/ngboost_interval_proxy_pilot_ootang_v1/reliability.csv",
            "figures/ngboost_interval_proxy_pilot_ootang_v1/manifest.json",
        ),
        warning_artifact_scope="exploratory_proxy_pilot",
        enabled_by_default=False,
    ),
    Stage(
        "ootang-ngboost-interval-proxy-horizon-sensitivity",
        "code/warning/ootang_ngboost_interval_proxy_horizon_sensitivity.py",
        "运行藕塘固定 NGBoost 的 h=1/3/7 区间代理提前量敏感性（显式、非排名）",
        inputs=(
            "config/ootang_ngboost_interval_proxy_horizon_sensitivity.v1.json",
            "config/ootang_ngboost_interval_proxy_pilot.v1.json",
            "figures/convlstm/forecast_predictions.csv",
            "figures/convlstm/forecast_run_manifest.json",
            "data/ootang_kinematics_long.csv",
            "figures/warning_operational_draft_v4/ootang_operational_thresholds.csv",
            "figures/warning_operational_draft_v4/ootang_operational_run_manifest.json",
            "models/ootang_ngboost_interval_proxy_pilot_v1.pkl",
            "figures/ngboost_interval_proxy_pilot_ootang_v1/predictions.csv",
            "figures/ngboost_interval_proxy_pilot_ootang_v1/metrics.csv",
            "figures/ngboost_interval_proxy_pilot_ootang_v1/confusion_matrices.csv",
            "figures/ngboost_interval_proxy_pilot_ootang_v1/reliability.csv",
            "figures/ngboost_interval_proxy_pilot_ootang_v1/manifest.json",
        ),
        outputs=(
            "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h1.pkl",
            "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h3.pkl",
            "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h7.pkl",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/predictions.csv",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/metrics.csv",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/confusion_matrices.csv",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/reliability.csv",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/horizon_summary.csv",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/manifest.json",
        ),
        warning_artifact_scope="exploratory_proxy_horizon_sensitivity",
        enabled_by_default=False,
    ),
    Stage(
        "ootang-ngboost-interval-proxy-feature-ablation",
        "code/warning/ootang_ngboost_interval_proxy_feature_ablation.py",
        "运行藕塘固定 NGBoost 的四指标分组消融（显式、非排名）",
        inputs=(
            "config/ootang_ngboost_interval_proxy_feature_ablation.v1.json",
            "config/ootang_ngboost_interval_proxy_horizon_sensitivity.v1.json",
            "config/ootang_ngboost_interval_proxy_pilot.v1.json",
            "figures/convlstm/forecast_predictions.csv",
            "figures/convlstm/forecast_run_manifest.json",
            "data/ootang_kinematics_long.csv",
            "figures/warning_operational_draft_v4/ootang_operational_thresholds.csv",
            "figures/warning_operational_draft_v4/ootang_operational_run_manifest.json",
            "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h1.pkl",
            "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h3.pkl",
            "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h7.pkl",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/predictions.csv",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/metrics.csv",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/confusion_matrices.csv",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/reliability.csv",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/horizon_summary.csv",
            "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/manifest.json",
        ),
        outputs=(
            "figures/ngboost_interval_proxy_feature_ablation_ootang_v1/predictions.csv",
            "figures/ngboost_interval_proxy_feature_ablation_ootang_v1/metrics.csv",
            "figures/ngboost_interval_proxy_feature_ablation_ootang_v1/confusion_matrices.csv",
            "figures/ngboost_interval_proxy_feature_ablation_ootang_v1/reliability.csv",
            "figures/ngboost_interval_proxy_feature_ablation_ootang_v1/ablation_summary.csv",
            "figures/ngboost_interval_proxy_feature_ablation_ootang_v1/manifest.json",
        ),
        warning_artifact_scope="exploratory_proxy_feature_ablation",
        enabled_by_default=False,
    ),
    Stage(
        "ootang-auto-v0-direct-bai-perron",
        "code/warning/auto_v0_direct_bai_perron.py",
        "运行藕塘 fit-only 原始位移自动 BIC 分段 V0 候选诊断（显式、非正式）",
        inputs=(
            "config/ootang_auto_v0_direct_bai_perron.v1.json",
            "data/ootang_kinematics_long.csv",
            "figures/convlstm/forecast_predictions.csv",
            "figures/convlstm/forecast_run_manifest.json",
            "figures/warning_operational_draft_v4/ootang_operational_thresholds.csv",
            "figures/warning_operational_draft_v4/ootang_operational_run_manifest.json",
        ),
        outputs=(
            "figures/auto_v0_direct_bai_perron_ootang_v1/candidates.csv",
            "figures/auto_v0_direct_bai_perron_ootang_v1/segments.csv",
            "figures/auto_v0_direct_bai_perron_ootang_v1/candidate_diagnostics.png",
            "figures/auto_v0_direct_bai_perron_ootang_v1/candidate_diagnostics.svg",
            "figures/auto_v0_direct_bai_perron_ootang_v1/manifest.json",
        ),
        warning_artifact_scope="exploratory_v0_candidate",
        enabled_by_default=False,
    ),
    Stage(
        "ootang-v5-candidate-display",
        "code/warning/ootang_v5_candidate_display.py",
        "展示藕塘自动 V0/ΔV/切线角候选输入及 unavailable 状态（显式、非融合）",
        inputs=(
            "config/ootang_v5_candidate_display.v1.json",
            "figures/auto_v0_direct_bai_perron_ootang_v1/candidates.csv",
            "figures/auto_v0_direct_bai_perron_ootang_v1/segments.csv",
            "figures/auto_v0_direct_bai_perron_ootang_v1/manifest.json",
            "data/ootang_kinematics_long.csv",
            "figures/convlstm/forecast_predictions.csv",
            "figures/convlstm/forecast_run_manifest.json",
            "figures/warning_operational_draft_v4/ootang_operational_thresholds.csv",
            "figures/warning_operational_draft_v4/ootang_operational_station_timeline.csv",
            "figures/warning_operational_draft_v4/ootang_operational_site_timeline.csv",
            "figures/warning_operational_draft_v4/ootang_operational_run_manifest.json",
        ),
        outputs=(
            "figures/v5_candidate_display_ootang_v1/candidate_timeline.csv",
            "figures/v5_candidate_display_ootang_v1/candidate_summary.csv",
            "figures/v5_candidate_display_ootang_v1/candidate_display.png",
            "figures/v5_candidate_display_ootang_v1/candidate_display.svg",
            "figures/v5_candidate_display_ootang_v1/manifest.json",
        ),
        warning_artifact_scope="exploratory_v5_candidate_display",
        enabled_by_default=False,
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


def git_worktree_is_dirty(root: Path = ROOT) -> bool | None:
    """Return whether tracked or untracked files differ from HEAD, if available."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


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
        "schema_version": 3,
        "status": "running",
        "warning_pipeline_scope": WARNING_PIPELINE_SCOPE,
        "formal_warning_output": False,
        "formal_warning_entry": FORMAL_WARNING_ENTRY,
        "started_at": timestamp(),
        "finished_at": None,
        "git_commit": current_git_commit(),
        "git_worktree_dirty": git_worktree_is_dirty(root),
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
            "input_artifacts": [],
            "outputs": [],
            "contract_error": None,
        }
        manifest["stages"].append(stage_result)
        input_paths = [root / path for path in stage.inputs]
        missing_inputs = [
            str(path.relative_to(root)) for path in input_paths if not path.is_file()
        ]
        stage_result["input_artifacts"] = [
            file_fingerprint(path, root) for path in input_paths if path.is_file()
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
