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
    arguments: tuple[str, ...] = ()
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
        "ootang-prequential-calibration-bakeoff",
        "code/monitoring/ootang_prequential_calibration_bakeoff.py",
        "比较藕塘 E1 prequential 校准候选方法（回顾性研究，非正式）",
        inputs=(
            "config/ootang_prequential_calibration_bakeoff.v1.json",
            "config/ootang_prequential_monitor.v1.json",
            "figures/prequential_anomaly_ootang_v1/station_timeline.csv",
            "figures/prequential_anomaly_ootang_v1/site_timeline.csv",
            "figures/prequential_anomaly_ootang_v1/prequential_metrics.csv",
            "figures/prequential_anomaly_ootang_v1/manifest.json",
        ),
        outputs=(
            "figures/prequential_calibration_bakeoff_ootang_v1/candidate_timeline.csv",
            "figures/prequential_calibration_bakeoff_ootang_v1/candidate_metrics.csv",
            "figures/prequential_calibration_bakeoff_ootang_v1/pairwise_comparison.csv",
            "figures/prequential_calibration_bakeoff_ootang_v1/manifest.json",
        ),
        arguments=(
            "--config",
            "config/ootang_prequential_calibration_bakeoff.v1.json",
        ),
        warning_artifact_scope="retrospective_prequential_calibration_bakeoff_research",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-live-source",
        "code/monitoring/ootang_live_source.py",
        "校验并内容寻址物化藕塘 E2-B 机器源快照（工程基础设施，非正式）",
        inputs=(
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "data/monitoring_data.csv",
        ),
        outputs=("runtime/ootang_prequential_live_v1/source_ingest_status.json",),
        arguments=("--config", "config/ootang_prequential_deploy.v1.json"),
        warning_artifact_scope="live_prequential_deployment_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-production-bundle",
        "code/convlstm/ootang_production_bundle.py",
        "构建藕塘 E2-B 五种子内容寻址安全 checkpoint bundle（工程基础设施，非正式）",
        inputs=(
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "data/monitoring_data.csv",
            "data/station_coords.csv",
            "pyproject.toml",
            "uv.lock",
        ),
        outputs=("runtime/ootang_prequential_live_v1/model_bundle_status.json",),
        arguments=("--config", "config/ootang_prequential_deploy.v1.json"),
        warning_artifact_scope="live_prequential_deployment_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-issue-producer",
        "code/monitoring/ootang_issue_producer.py",
        "从五个安全 checkpoint 自动重放下一自然日 issue（工程基础设施，非正式）",
        inputs=(
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "data/monitoring_data.csv",
            "data/station_coords.csv",
            "pyproject.toml",
            "uv.lock",
        ),
        outputs=("runtime/ootang_prequential_live_v1/issue_producer_status.json",),
        arguments=("--config", "config/ootang_prequential_deploy.v1.json"),
        warning_artifact_scope="live_prequential_deployment_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-outcome-materializer",
        "code/monitoring/ootang_outcome_materializer.py",
        "从已验证 finalized source 自动物化 E2-B2 outcome（工程基础设施，非正式）",
        inputs=(
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "data/monitoring_data.csv",
            "pyproject.toml",
            "uv.lock",
        ),
        outputs=(
            "runtime/ootang_prequential_live_v1/outcome_materializer_status.json",
        ),
        arguments=("--config", "config/ootang_prequential_cycle.v1.json"),
        warning_artifact_scope="live_prequential_cycle_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-prequential-live",
        "code/monitoring/ootang_prequential_live.py",
        "执行藕塘 E2-A 机器轮询、追加式事件账本与完整性状态更新（工程基础设施，非正式）",
        inputs=(
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_monitor.v1.json",
            "figures/prequential_anomaly_ootang_v1/manifest.json",
        ),
        outputs=("runtime/ootang_prequential_live_v1/status.json",),
        arguments=("--config", "config/ootang_prequential_live.v1.json"),
        warning_artifact_scope="live_prequential_monitoring_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-prequential-cycle",
        "code/monitoring/ootang_prequential_cycle.py",
        "执行藕塘 E2-B2 source→outcome→issue fixed-point 机器闭环（工程基础设施，非正式）",
        inputs=(
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "data/monitoring_data.csv",
            "data/station_coords.csv",
            "pyproject.toml",
            "uv.lock",
        ),
        outputs=("runtime/ootang_prequential_live_v1/cycle_status.json",),
        arguments=("--config", "config/ootang_prequential_cycle.v1.json"),
        warning_artifact_scope="live_prequential_cycle_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-prequential-calibration-shadow",
        "code/monitoring/ootang_prequential_calibration_shadow.py",
        "运行藕塘三候选 E2 校准影子账本与机器 issue/reveal 对账（工程基础设施，非正式）",
        inputs=(
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_calibration_bakeoff.v1.json",
            "code/monitoring/calibration_challengers.py",
            "pyproject.toml",
            "uv.lock",
        ),
        outputs=("runtime/ootang_prequential_calibration_shadow_v1/status.json",),
        arguments=(
            "--config",
            "config/ootang_prequential_calibration_shadow.v1.json",
        ),
        warning_artifact_scope="live_prequential_calibration_shadow_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-prequential-cycle-v2",
        "code/monitoring/ootang_prequential_cycle_v2.py",
        "执行含校准影子因果屏障的藕塘 E2 fixed-point 机器闭环（工程基础设施，非正式）",
        inputs=(
            "config/ootang_prequential_cycle.v2.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "data/monitoring_data.csv",
            "data/station_coords.csv",
            "pyproject.toml",
            "uv.lock",
        ),
        outputs=("runtime/ootang_prequential_live_v1/cycle_v2_status.json",),
        arguments=("--config", "config/ootang_prequential_cycle.v2.json"),
        warning_artifact_scope="live_prequential_calibration_cycle_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-issue-replay",
        "code/monitoring/ootang_issue_replay.py",
        "独立重放并校验藕塘 issue 的 checkpoint、输入与预测（工程基础设施，非正式）",
        inputs=(
            "config/ootang_issue_replay.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "code/monitoring/ootang_live_source.py",
            "data/monitoring_data.csv",
            "data/station_coords.csv",
            "pyproject.toml",
            "uv.lock",
        ),
        outputs=("runtime/ootang_prequential_live_v1/issue_replay_status.json",),
        arguments=("--config", "config/ootang_issue_replay.v1.json"),
        warning_artifact_scope="live_prequential_issue_replay_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-verified-live",
        "code/monitoring/ootang_verified_live.py",
        "通过独立 issue 重放门控推进藕塘 live 账本（工程基础设施，非正式）",
        inputs=(
            "config/ootang_verified_live.v1.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_monitor.v1.json",
            "data/monitoring_data.csv",
            "data/station_coords.csv",
            "pyproject.toml",
            "uv.lock",
        ),
        outputs=("runtime/ootang_prequential_live_v1/verified_live_status.json",),
        arguments=("--config", "config/ootang_verified_live.v1.json"),
        warning_artifact_scope="live_prequential_verified_entrypoint_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-prequential-cycle-v3",
        "code/monitoring/ootang_prequential_cycle_v3.py",
        "执行含独立重放门控与校准影子因果屏障的藕塘 E2 fixed-point 机器闭环（工程基础设施，非正式）",
        inputs=(
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_prequential_cycle.v2.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "code/monitoring/ootang_live_source.py",
            "code/monitoring/calibration_challengers.py",
            "data/monitoring_data.csv",
            "data/station_coords.csv",
            "pyproject.toml",
            "uv.lock",
        ),
        outputs=("runtime/ootang_prequential_live_v1/cycle_v3_status.json",),
        arguments=("--config", "config/ootang_prequential_cycle.v3.json"),
        warning_artifact_scope=(
            "live_prequential_replay_gated_calibration_cycle_engineering"
        ),
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-trusted-time-shadow",
        "code/monitoring/ootang_trusted_time_shadow.py",
        "为 replay-gated issue seal 获取并离线复验 RFC 3161 可信时间回执（影子工程门，非正式）",
        inputs=(
            "config/ootang_trusted_time_shadow.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/trust/sigstore_tsa_2025_manifest.v1.json",
            "config/trust/sigstore_tsa_2025_leaf.pem",
            "config/trust/sigstore_tsa_2025_root.pem",
            "code/monitoring/ootang_trusted_time_shadow_core.py",
            "tools/ootang_trusted_time_runtime/pyproject.toml",
            "tools/ootang_trusted_time_runtime/uv.lock",
        ),
        outputs=("runtime/ootang_prequential_live_v1/trusted_time_shadow_status.json",),
        arguments=(
            "--config",
            "config/ootang_trusted_time_shadow.v1.json",
        ),
        warning_artifact_scope=("live_prequential_trusted_time_shadow_engineering"),
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-epoch-registry",
        "code/monitoring/ootang_epoch_registry.py",
        "在稳定 slot 中预构建并登记不可变 epoch candidate snapshots（R1 registry，非轮换/非正式）",
        inputs=(
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "config/trust/sigstore_tsa_2025_manifest.v1.json",
            "config/trust/sigstore_tsa_2025_leaf.pem",
            "config/trust/sigstore_tsa_2025_root.pem",
            "pyproject.toml",
            "uv.lock",
            "tools/ootang_trusted_time_runtime/pyproject.toml",
            "tools/ootang_trusted_time_runtime/uv.lock",
        ),
        outputs=("runtime/ootang_epoch_registry_v1/registry_status.json",),
        arguments=(
            "--config",
            "config/ootang_epoch_registry.v1.json",
        ),
        warning_artifact_scope="epoch_candidate_registry_r1_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-epoch-preparation",
        "code/monitoring/ootang_epoch_preparation.py",
        "把 R1 candidate 物化为同源可执行预检树并执行双隔离环境/五种子重放烟测（R2a，非轮换/非正式）",
        inputs=(
            "config/ootang_epoch_preparation.v1.json",
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "code/convlstm/__init__.py",
            "code/monitoring/__init__.py",
            "pyproject.toml",
            "uv.lock",
            "tools/ootang_trusted_time_runtime/pyproject.toml",
            "tools/ootang_trusted_time_runtime/uv.lock",
        ),
        outputs=("runtime/ootang_epoch_registry_v1/preparation_status.json",),
        arguments=(
            "--config",
            "config/ootang_epoch_preparation.v1.json",
        ),
        warning_artifact_scope="epoch_candidate_same_origin_preflight_r2a_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-epoch-drain",
        "code/monitoring/ootang_epoch_drain.py",
        "原子撤销旧 epoch canonical issue route 并追加机器排空起始屏障（R2b，非切换/非正式）",
        inputs=(
            "config/ootang_epoch_drain.v1.json",
            "config/ootang_epoch_preparation.v1.json",
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
        ),
        outputs=("runtime/ootang_epoch_registry_v1/drain_status.json",),
        arguments=(
            "--config",
            "config/ootang_epoch_drain.v1.json",
        ),
        warning_artifact_scope="epoch_drain_barrier_r2b_engineering",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-epoch-drain-eligibility",
        "code/monitoring/ootang_epoch_drain_eligibility.py",
        "持久化并复验旧 epoch 排空资格观察，自动识别过期观察（R2b-2a，仅 DRAINING/非切换/非正式）",
        inputs=(
            "config/ootang_epoch_drain_eligibility.v1.json",
            "config/ootang_epoch_drain.v1.json",
            "config/ootang_epoch_preparation.v1.json",
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "code/monitoring/ootang_epoch_drain.py",
        ),
        outputs=("runtime/ootang_epoch_registry_v1/drain_eligibility_status.json",),
        arguments=(
            "--config",
            "config/ootang_epoch_drain_eligibility.v1.json",
        ),
        warning_artifact_scope=(
            "epoch_drain_eligibility_observation_r2b_2a_engineering"
        ),
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-epoch-drain-v2-workset",
        "code/monitoring/ootang_epoch_drain_v2.py",
        "观察机器当前旧 epoch 首个排空阻塞项（R2b-2b-1，非 DRAINING authority/非切换/非正式）",
        inputs=(
            "config/ootang_epoch_drain.v2.json",
            "config/ootang_epoch_drain.v1.json",
            "config/ootang_epoch_drain_eligibility.v1.json",
            "config/ootang_epoch_preparation.v1.json",
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_prequential_cycle.v1.json",
            "config/ootang_prequential_cycle.v3.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "code/monitoring/ootang_epoch_drain.py",
            "code/monitoring/ootang_epoch_drain_eligibility.py",
            "code/monitoring/ootang_issue_replay.py",
            "code/monitoring/ootang_verified_live.py",
            "code/monitoring/ootang_prequential_calibration_shadow.py",
            "code/monitoring/ootang_trusted_time_shadow_core.py",
        ),
        outputs=("runtime/ootang_epoch_registry_v1/drain_v2/status.json",),
        arguments=(
            "--config",
            "config/ootang_epoch_drain.v2.json",
        ),
        warning_artifact_scope=(
            "epoch_drain_first_blocker_observation_r2b_2b_1_v2_engineering"
        ),
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-epoch-admission-cut",
        "code/monitoring/ootang_epoch_admission_cut.py",
        "物理封闭旧 epoch 官方 writer 的 deploy/runner 锁路径（R2b-2b-2a，非 closed workset/非切换/非正式）",
        inputs=(
            "config/ootang_epoch_admission_cut.v1.json",
            "config/ootang_epoch_drain.v2.json",
            "config/ootang_epoch_drain.v1.json",
            "code/monitoring/ootang_epoch_drain_v2.py",
            "code/monitoring/ootang_epoch_drain.py",
            "code/monitoring/ootang_live_source.py",
            "code/monitoring/ootang_issue_producer.py",
            "code/monitoring/ootang_outcome_materializer.py",
            "code/monitoring/ootang_prequential_live.py",
            "code/monitoring/ootang_verified_live.py",
            "code/monitoring/ootang_issue_replay.py",
            "code/monitoring/ootang_trusted_time_shadow_core.py",
            "code/monitoring/ootang_prequential_calibration_shadow.py",
            "code/monitoring/ootang_prequential_cycle.py",
            "code/monitoring/ootang_prequential_cycle_v2.py",
            "code/monitoring/ootang_prequential_cycle_v3.py",
        ),
        outputs=("runtime/ootang_epoch_registry_v1/admission_cut_v1/status.json",),
        arguments=(
            "--config",
            "config/ootang_epoch_admission_cut.v1.json",
        ),
        warning_artifact_scope=(
            "epoch_admission_writer_lock_cut_r2b_2b_2a_engineering"
        ),
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-epoch-workset-manifest",
        "code/monitoring/ootang_epoch_workset_manifest.py",
        "预留旧 epoch 冻结观测六族 workset 与 transition seed（R2b-2b-2b，非终态闭包/非切换/非正式）",
        inputs=(
            "config/ootang_epoch_workset_manifest.v1.json",
            "config/ootang_epoch_admission_cut.v1.json",
            "config/ootang_epoch_drain.v2.json",
            "config/ootang_prequential_deploy.v1.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_issue_replay.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "config/ootang_prequential_calibration_shadow.v1.json",
            "code/monitoring/ootang_epoch_admission_cut.py",
            "code/monitoring/ootang_epoch_drain_v2.py",
            "code/monitoring/ootang_epoch_workset_inventory.py",
            "code/monitoring/ootang_live_source.py",
            "code/monitoring/ootang_issue_producer.py",
            "code/monitoring/ootang_outcome_materializer.py",
            "code/monitoring/ootang_prequential_live.py",
            "code/monitoring/ootang_verified_live.py",
            "code/monitoring/ootang_issue_replay.py",
            "code/monitoring/ootang_trusted_time_shadow_core.py",
            "code/monitoring/ootang_prequential_calibration_shadow.py",
        ),
        outputs=("runtime/ootang_epoch_registry_v1/workset_manifest_v1/status.json",),
        arguments=(
            "--config",
            "config/ootang_epoch_workset_manifest.v1.json",
        ),
        warning_artifact_scope=(
            "epoch_closed_workset_manifest_reservation_r2b_2b_2b_engineering"
        ),
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-epoch-workset-recovery",
        "code/monitoring/ootang_epoch_workset_recovery.py",
        "按 transition plan 单步推进旧 epoch 恢复，含 machine-only anchor request、四锁外响应观测与四锁内 result CAS（R2b-2b-2d，非切换/非正式）",
        inputs=(
            "config/ootang_epoch_workset_recovery.v1.json",
            "config/ootang_epoch_workset_manifest.v1.json",
            "config/ootang_epoch_admission_cut.v1.json",
            "config/ootang_epoch_drain.v2.json",
            "config/ootang_prequential_live.v1.json",
            "config/ootang_verified_live.v1.json",
            "config/ootang_trusted_time_shadow.v1.json",
            "code/monitoring/ootang_epoch_workset_recovery.py",
            "code/monitoring/ootang_epoch_workset_manifest.py",
            "code/monitoring/ootang_epoch_admission_cut.py",
            "code/monitoring/ootang_epoch_drain.py",
            "code/monitoring/ootang_epoch_drain_v2.py",
            "code/monitoring/ootang_epoch_registry.py",
            "code/monitoring/ootang_prequential_live.py",
            "code/monitoring/ootang_live_ledger.py",
            "code/monitoring/ootang_live_ledger_cas_v1.py",
            "code/monitoring/ootang_verified_live.py",
            "code/monitoring/ootang_trusted_time_shadow_core.py",
        ),
        outputs=("runtime/ootang_epoch_registry_v1/workset_recovery_v1/status.json",),
        arguments=(
            "--config",
            "config/ootang_epoch_workset_recovery.v1.json",
        ),
        warning_artifact_scope=(
            "epoch_anchor_request_event_recovery_r2b_2b_2d_engineering"
        ),
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-epoch-settlement-cycle",
        "code/monitoring/ootang_epoch_settlement_cycle.py",
        "自动执行 manifest 后 settlement 与 V2 scoped drain-completion 单次有界轮询（非全局 drained/非切换/非正式）",
        inputs=(
            "config/ootang_epoch_workset_recovery.v1.json",
            "config/ootang_epoch_step_dependency_reservation.v1.json",
            "config/ootang_epoch_step_dependency_overlay.v1.json",
            "config/ootang_epoch_source_terminal_aggregate.v1.json",
            "config/ootang_epoch_manifest_terminal_coverage.v1.json",
            "config/ootang_epoch_source_ingest_derived_reservation.v1.json",
            "config/ootang_epoch_source_ingest_cross_freeze.v1.json",
            "config/ootang_epoch_source_derived_workset_overlay.v1.json",
            "config/ootang_epoch_source_derived_outcome_dispatch.v1.json",
            "config/ootang_epoch_source_derived_outcome_consumption.v1.json",
            "config/ootang_epoch_source_derived_dependent_outcome_dispatch.v1.json",
            "config/ootang_epoch_source_derived_dependent_outcome_consumption.v1.json",
            "config/ootang_epoch_source_derived_effective_outcome_terminal_coverage.v1.json",
            "config/ootang_epoch_source_derived_source_parent_terminal_aggregate.v1.json",
            "config/ootang_epoch_source_derived_retained_base_terminal_coverage.v1.json",
            "config/ootang_epoch_source_derived_current_effective_workset_terminal_coverage.v1.json",
            "config/ootang_epoch_source_derived_bounded_terminal_closure.v1.json",
        ),
        outputs=(
            "runtime/ootang_epoch_registry_v1/settlement_cycle_v1/status.json",
            "runtime/ootang_epoch_registry_v1/workset_recovery_v1/bounded_drain_completion_v1/status.json",
        ),
        arguments=(),
        warning_artifact_scope=(
            "epoch_post_manifest_machine_settlement_and_v2_drain_completion_engineering"
        ),
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-epoch-active-transition",
        "code/monitoring/ootang_epoch_active_transition.py",
        "原子提交旧 epoch SEALED 与新 epoch ACTIVE，并签发限定官方调度器授权（非全局 drain/非正式）",
        inputs=(
            "config/ootang_epoch_registry.v1.json",
            "config/ootang_epoch_preparation.v1.json",
            "config/ootang_epoch_drain.v1.json",
            "config/ootang_prequential_cycle.v3.json",
        ),
        outputs=("runtime/ootang_epoch_registry_v1/active_transition_v1/status.json",),
        arguments=(),
        warning_artifact_scope=(
            "epoch_scoped_official_scheduler_lifecycle_transition_engineering"
        ),
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-prequential-cycle-v4",
        "code/monitoring/ootang_prequential_cycle_v4.py",
        "按 ACTIVE 转移事件派生唯一 live/shadow root 并运行冻结 cycle-v3（官方调度入口，非正式）",
        outputs=("runtime/ootang_epoch_registry_v1/scheduler_cycle_v4_v1/status.json",),
        arguments=(),
        warning_artifact_scope=(
            "live_prequential_authorized_scheduler_cycle_engineering"
        ),
        formal_warning_output=False,
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
    Stage(
        "ootang-ngboost-auto-state",
        "code/warning/ootang_ngboost_auto_state.py",
        "生成藕塘未来 7 日自动变形状态标签与四指标 OOF 诊断（显式、非正式）",
        inputs=(
            "config/ootang_ngboost_auto_state.v1.json",
            "data/ootang_kinematics_long.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/seed_stability_predictions.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/seed_stability_runs.csv",
            f"{CONVLSTM_DIAGNOSTIC_ROOT}/seed_stability_0_4/manifest.json",
            "config/ootang_operational_run.v4.draft.json",
        ),
        outputs=(
            "figures/ngboost_auto_state_v1/station_auto_labels.csv",
            "figures/ngboost_auto_state_v1/site_auto_labels.csv",
            "figures/ngboost_auto_state_v1/label_state_definition.csv",
            "figures/ngboost_auto_state_v1/label_gate.json",
            "figures/ngboost_auto_state_v1/auto_state_timeline.png",
            "figures/ngboost_auto_state_v1/manifest.json",
        ),
        arguments=("--config", "config/ootang_ngboost_auto_state.v1.json"),
        warning_artifact_scope="exploratory_auto_future_state_proxy",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-ngboost-auto-state-ecdf",
        "code/warning/ootang_ngboost_auto_state_ecdf.py",
        "生成藕塘 fold-1 ECDF 五级未来变形状态 challenger（显式、非正式）",
        inputs=(
            "config/ootang_ngboost_auto_state_ecdf.v2.json",
            "figures/ngboost_auto_state_v1/station_auto_labels.csv",
            "figures/ngboost_auto_state_v1/manifest.json",
            "config/ootang_operational_run.v4.draft.json",
        ),
        outputs=(
            "figures/ngboost_auto_state_ecdf_v2/station_auto_labels.csv",
            "figures/ngboost_auto_state_ecdf_v2/site_auto_labels.csv",
            "figures/ngboost_auto_state_ecdf_v2/label_state_definition.csv",
            "figures/ngboost_auto_state_ecdf_v2/label_gate.json",
            "figures/ngboost_auto_state_ecdf_v2/auto_state_timeline.png",
            "figures/ngboost_auto_state_ecdf_v2/manifest.json",
        ),
        arguments=("--config", "config/ootang_ngboost_auto_state_ecdf.v2.json"),
        warning_artifact_scope="exploratory_auto_future_state_ecdf_proxy",
        formal_warning_output=False,
        enabled_by_default=False,
    ),
    Stage(
        "ootang-ngboost-auto-state-classifier",
        "code/warning/ootang_ngboost_auto_state_classifier.py",
        "训练藕塘固定 NGBoost 五级概率模型并输出全时刻 site/八点诊断（显式、非正式）",
        inputs=(
            "config/ootang_ngboost_auto_state_classifier.v1.json",
            "figures/ngboost_auto_state_ecdf_v2/station_auto_labels.csv",
            "figures/ngboost_auto_state_ecdf_v2/site_auto_labels.csv",
            "figures/ngboost_auto_state_ecdf_v2/label_gate.json",
            "figures/ngboost_auto_state_ecdf_v2/manifest.json",
        ),
        outputs=(
            "figures/ngboost_auto_state_classifier_v1/site_predictions.csv",
            "figures/ngboost_auto_state_classifier_v1/station_predictions.csv",
            "figures/ngboost_auto_state_classifier_v1/metrics.csv",
            "figures/ngboost_auto_state_classifier_v1/confusion_matrix.csv",
            "figures/ngboost_auto_state_classifier_v1/feature_importance.csv",
            "figures/ngboost_auto_state_classifier_v1/site_shap_values.csv",
            "figures/ngboost_auto_state_classifier_v1/site_shap_importance.csv",
            "figures/ngboost_auto_state_classifier_v1/site_shap_summary.png",
            "figures/ngboost_auto_state_classifier_v1/site_shap_summary.pdf",
            "figures/ngboost_auto_state_classifier_v1/site_shap_summary.svg",
            "figures/ngboost_auto_state_classifier_v1/warning_timeline.png",
            "figures/ngboost_auto_state_classifier_v1/warning_timeline.pdf",
            "figures/ngboost_auto_state_classifier_v1/warning_timeline.svg",
            "figures/ngboost_auto_state_classifier_v1/manifest.json",
            "models/ootang_ngboost_auto_state_site_v1.pkl",
            "models/ootang_ngboost_auto_state_station_diagnostic_v1.pkl",
        ),
        arguments=(
            "--config",
            "config/ootang_ngboost_auto_state_classifier.v1.json",
        ),
        warning_artifact_scope="exploratory_auto_future_state_ngboost_classifier",
        formal_warning_output=False,
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
        command = [sys.executable, str(root / stage.script), *stage.arguments]
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
            selection_scope = "default" if stage.enabled_by_default else "explicit-only"
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
