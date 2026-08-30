"""Materialize the registered Ootang advisor tables without model execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "ootang_advisor_package.v1.json"
LEVELS = ("green", "blue", "yellow", "orange", "red")
PACKAGE_STATUS = "advisor_research_demo_not_formal"


class PackageInputError(RuntimeError):
    """Raised when a registered source no longer matches the reporting contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _require(frame: pd.DataFrame, columns: set[str], name: str) -> None:
    missing = columns - set(frame.columns)
    if missing:
        raise PackageInputError(f"{name} missing columns: {sorted(missing)}")


def _load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    fixed = {
        "schema_version": 1,
        "profile_id": "ootang_advisor_package_v1",
        "case": "ootang",
        "vajont_used": False,
        "formal_warning_output": False,
        "package_status": PACKAGE_STATUS,
    }
    if any(config.get(key) != value for key, value in fixed.items()):
        raise PackageInputError("Package config violates its fixed non-formal contract")
    if len(config.get("inputs", {})) != 12 or len(config.get("outputs", {})) != 7:
        raise PackageInputError("Package config has an unexpected input/output set")
    if set(config.get("display_assets", {})) != {
        "convlstm_all_stations", "auto_state_timeline", "classifier_shap",
        "v4_station_diagnostic", "v4_site_timeline",
    }:
        raise PackageInputError("Package config must register exactly five display assets")
    return config


def _table1(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "fold", "scope", "n_dates", "n_stations", "seed_count",
        "model_rmse_mean", "model_rmse_std", "model_mae_mean", "model_mae_std",
        "baseline_rmse", "baseline_mae", "rmse_skill_vs_baseline_mean",
        "mae_skill_vs_baseline_mean", "rmse_skill_positive_seeds",
        "mae_skill_positive_seeds", "increment_correlation_mean",
        "increment_std_ratio_mean", "coverage_mean", "coverage_std",
        "mean_width_mean", "interval_score_80_mean",
    ]
    _require(frame, set(columns) | {"interval_variant", "best_seed_selected"}, "ConvLSTM summary")
    result = frame.loc[frame["interval_variant"].eq("calibrated"), columns].copy()
    if len(result) != 27 or set(result["scope"]) != {"overall", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9"}:
        raise PackageInputError("ConvLSTM table must contain 3 folds x 9 scopes")
    if frame.loc[frame["interval_variant"].eq("calibrated"), "best_seed_selected"].astype(bool).any():
        raise PackageInputError("ConvLSTM package cannot contain best-seed selection")
    order = {name: index for index, name in enumerate(("overall", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9"))}
    result["_order"] = result["scope"].map(order)
    return result.sort_values(["fold", "_order"]).drop(columns="_order").reset_index(drop=True)


def _table2(definition: pd.DataFrame, labels: pd.DataFrame, gate: dict[str, Any]) -> pd.DataFrame:
    boundary_columns = ["record_type", "scope", "boundary_index", "quantile_probability", "boundary_value", "quantile_method", "fit_fold", "max_input_date", "boundary_version"]
    _require(definition, set(boundary_columns), "label definition")
    boundaries = definition.loc[definition["record_type"].isin(["station_boundary", "site_boundary"]), boundary_columns].copy()
    if len(boundaries) != 8:
        raise PackageInputError("Auto-label table requires eight fixed boundaries")
    boundaries.insert(0, "panel", "boundary")
    boundaries["fold"] = pd.NA
    boundaries["auto_state_level"] = pd.NA
    boundaries["auto_state_color"] = pd.NA
    boundaries["support_n"] = pd.NA
    _require(labels, {"fold", "label_status", "auto_state_level", "auto_state_color"}, "site labels")
    support = (
        labels.loc[labels["fold"].isin([1, 2]) & labels["label_status"].eq("valid")]
        .groupby(["fold", "auto_state_level", "auto_state_color"], observed=True)
        .size().rename("support_n").reset_index()
    )
    if len(support) != 10:
        raise PackageInputError("Auto-label support must contain five levels in folds 1 and 2")
    expected = {1: [56, 56, 56, 56, 56], 2: [138, 66, 39, 21, 16]}
    support["_order"] = pd.Categorical(support["auto_state_color"], LEVELS, ordered=True)
    support = support.sort_values(["fold", "_order"]).drop(columns="_order")
    for fold, counts in expected.items():
        if support.loc[support["fold"].eq(fold), "support_n"].tolist() != counts:
            raise PackageInputError(f"fold {fold} auto-label support changed")
    support.insert(0, "panel", "support")
    for column in boundary_columns:
        support[column] = pd.NA
    if gate.get("label_gate_passed") is not True or gate.get("formal_warning_output") is not False:
        raise PackageInputError("ECDF label gate must be passed and non-formal")
    columns = ["panel", *boundary_columns, "fold", "auto_state_level", "auto_state_color", "support_n"]
    records = boundaries[columns].to_dict("records") + support[columns].to_dict("records")
    return pd.DataFrame.from_records(records, columns=columns)


def _table3(
    classifier: pd.DataFrame,
    memory_metrics: pd.DataFrame,
    memory_manifest: dict[str, Any],
    residual_metrics: pd.DataFrame,
    residual_manifest: dict[str, Any],
) -> pd.DataFrame:
    metrics = {"accuracy", "macro_f1_fixed_five", "ordinal_mae", "multiclass_log_loss", "multiclass_brier"}
    panel_a = classifier.loc[
        classifier["fold"].eq(2)
        & classifier["subset"].eq("all_valid")
        & classifier["estimator"].isin(["ngboost", "multinomial_logistic", "fold1_prior"])
        & classifier["metric"].isin(metrics)
        & classifier["class_level"].isna(),
        ["fold", "fold_role", "estimator", "n", "metric", "value", "status"],
    ].copy()
    panel_a.insert(0, "panel", "all_valid_280")
    panel_a["decision_status"] = "exploratory_negative_result"
    memory_common = memory_metrics.loc[
        memory_metrics["fold"].eq(2)
        & memory_metrics["scope"].eq("lag7_common")
        & memory_metrics["estimator"].eq("memory_ngboost")
        & memory_metrics["metric"].isin(metrics),
        ["fold", "fold_role", "estimator", "n", "metric", "value", "status"],
    ].copy()
    residual_common = residual_metrics.loc[
        residual_metrics["fold"].eq(2) & residual_metrics["scope"].eq("lag7_common") & residual_metrics["metric"].isin(metrics),
        ["fold", "fold_role", "estimator", "n", "metric", "value"],
    ].copy()
    residual_common["status"] = "reported_descriptive"
    panel_b = pd.concat([memory_common, residual_common], ignore_index=True)
    panel_b.insert(0, "panel", "common_273")
    panel_b["decision_status"] = panel_b["estimator"].map({
        "memory_ngboost": "rejected", "residual_ngboost": "rejected",
        "v1_ngboost": "exploratory_negative_result", "lag7_persistence": "causal_hard_baseline",
    })
    if len(panel_a) != 15 or len(panel_b) != 18 or set(panel_b["n"]) != {273}:
        raise PackageInputError("Classifier comparison row contract changed")
    memory_decision = memory_manifest.get("comparison", {}).get("decision", {})
    residual_decision = residual_manifest.get("comparison", {}).get("decision_result", {})
    if memory_decision.get("passed") is not False or residual_decision.get("passed") is not False:
        raise PackageInputError("Registered memory/residual rejection is missing")
    return pd.concat([panel_a, panel_b], ignore_index=True)


def _counts(panel: str, category_type: str, series: pd.Series, categories: tuple[str, ...]) -> pd.DataFrame:
    counts = series.value_counts(dropna=False)
    return pd.DataFrame({
        "panel": panel,
        "category_type": category_type,
        "category": list(categories),
        "count": [int(counts.get(category, 0)) for category in categories],
    })


def _table4(station: pd.DataFrame, site: pd.DataFrame) -> pd.DataFrame:
    _require(station, {"candidate_color", "acceleration_color", "station_assessment_status"}, "station timeline")
    _require(site, {"site_fusion_status", "site_confirmed_color", "local_max_candidate_color", "assessable_station_count", "assessable_block_count"}, "site timeline")
    if len(station) != 4112 or not station["station_assessment_status"].eq("valid").all():
        raise PackageInputError("Station timeline must contain 4,112 valid rows")
    if len(site) != 514 or set(site["assessable_station_count"]) != {8} or set(site["assessable_block_count"]) != {3}:
        raise PackageInputError("Site timeline must contain 514 complete 8-station/3-block dates")
    confirmed = site["site_confirmed_color"].fillna("not_confirmed")
    frames = [
        _counts("station", "candidate_color", station["candidate_color"], LEVELS),
        _counts("station", "acceleration_color", station["acceleration_color"], LEVELS),
        _counts("site", "fusion_status", site["site_fusion_status"], ("valid", "candidate_not_site_confirmed")),
        _counts("site", "confirmed_color", confirmed, (*LEVELS, "not_confirmed")),
        _counts("site", "local_max_color", site["local_max_candidate_color"], LEVELS),
    ]
    result = pd.concat(frames, ignore_index=True)
    expected = {
        ("station", "candidate_color"): [1119, 1895, 472, 276, 350],
        ("station", "acceleration_color"): [4012, 98, 2, 0, 0],
        ("site", "fusion_status"): [114, 400],
        ("site", "confirmed_color"): [8, 48, 31, 9, 18, 400],
        ("site", "local_max_color"): [0, 56, 196, 111, 151],
    }
    for key, values in expected.items():
        actual = result.loc[result["panel"].eq(key[0]) & result["category_type"].eq(key[1]), "count"].tolist()
        if actual != values:
            raise PackageInputError(f"Multistation count changed for {key}: {actual}")
    return result


def _table_s1(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "station", "fit_segment_start_date", "fit_segment_end_date", "acceleration_fit_segment_n",
        "acceleration_fit_mean_mm_per_day_squared", "acceleration_fit_sigma_ddof1_mm_per_day_squared",
        "acceleration_a0_mm_per_day_squared", "acceleration_green_upper_mm_per_day_squared",
        "acceleration_blue_lower_mm_per_day_squared", "acceleration_blue_upper_mm_per_day_squared",
        "acceleration_yellow_lower_mm_per_day_squared", "acceleration_yellow_upper_mm_per_day_squared",
        "acceleration_orange_lower_mm_per_day_squared", "acceleration_orange_upper_mm_per_day_squared",
        "acceleration_red_lower_mm_per_day_squared", "acceleration_formula", "acceleration_unit",
        "acceleration_a0_formula", "acceleration_threshold_status", "acceleration_baseline_source",
        "artifact_status", "formal_warning_output",
    ]
    _require(frame, set(columns), "acceleration thresholds")
    result = frame.loc[:, columns].copy()
    order = ("ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9")
    if len(result) != 8 or set(result["station"]) != set(order):
        raise PackageInputError("Acceleration threshold table must contain all eight stations")
    if result["formal_warning_output"].astype(bool).any():
        raise PackageInputError("Acceleration thresholds must remain non-formal")
    result["_order"] = result["station"].map({value: index for index, value in enumerate(order)})
    return result.sort_values("_order").drop(columns="_order").reset_index(drop=True)


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if pd.isna(value):
            return "N/A"
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value).replace("|", "\\|")

    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _summary(frames: dict[str, pd.DataFrame], assets: dict[str, Path], output_dir: Path) -> str:
    table1 = frames["table1_convlstm"]
    overall = table1.loc[table1["scope"].eq("overall")]
    convlstm = _markdown_table(
        ["fold", "RMSE mean±SD", "persistence RMSE", "RMSE skill", "coverage", "width", "interval score"],
        [[row.fold, f"{row.model_rmse_mean:.3f}±{row.model_rmse_std:.3f}", row.baseline_rmse, row.rmse_skill_vs_baseline_mean, row.coverage_mean, row.mean_width_mean, row.interval_score_80_mean] for row in overall.itertuples()],
    )
    support = frames["table2_auto_labels"].query("panel == 'support'").copy()
    support_table = _markdown_table(
        ["fold", *LEVELS],
        [[fold, *[int(support.loc[support["fold"].eq(fold) & support["auto_state_color"].eq(level), "support_n"].iloc[0]) for level in LEVELS]] for fold in (1, 2)],
    )
    comparison = frames["table3_classifier_comparison"]
    metric_order = ("accuracy", "macro_f1_fixed_five", "ordinal_mae", "multiclass_log_loss", "multiclass_brier")

    def comparison_table(panel: str, estimators: tuple[str, ...]) -> str:
        selected = comparison.loc[comparison["panel"].eq(panel)]
        rows = []
        for estimator in estimators:
            subset = selected.loc[selected["estimator"].eq(estimator)]
            values = [subset.loc[subset["metric"].eq(metric), "value"] for metric in metric_order]
            rows.append([estimator, int(subset["n"].iloc[0]), *[value.iloc[0] if len(value) else pd.NA for value in values], subset["decision_status"].iloc[0]])
        return _markdown_table(["estimator", "n", "accuracy", "macro-F1", "ordinal MAE", "log-loss", "Brier", "status"], rows)

    panel_a = comparison_table("all_valid_280", ("ngboost", "multinomial_logistic", "fold1_prior"))
    panel_b = comparison_table("common_273", ("v1_ngboost", "memory_ngboost", "residual_ngboost", "lag7_persistence"))
    table4 = frames["table4_multistation_summary"]

    def count_row(category_type: str, categories: tuple[str, ...]) -> list[Any]:
        selected = table4.loc[table4["category_type"].eq(category_type)]
        return [category_type, *[int(selected.loc[selected["category"].eq(category), "count"].iloc[0]) for category in categories]]

    v4 = _markdown_table(
        ["axis", *LEVELS, "not confirmed"],
        [
            count_row("confirmed_color", (*LEVELS, "not_confirmed")),
            count_row("local_max_color", LEVELS) + [pd.NA],
        ],
    )
    uses = {
        "convlstm_all_stations": "8 点训练/校准/预测段位移及 P10/P50/P90",
        "auto_state_timeline": "H=7 site 与 8 点逐时五色信号",
        "classifier_shap": "固定 site NGBoost 的测点×指标依赖",
        "v4_station_diagnostic": "v4 全测点四指标透明诊断",
        "v4_site_timeline": "v4 site-confirmed 与 local-max 双轴时间线",
    }
    figure_lines = [f"- [{name}]({Path(os.path.relpath(path, output_dir)).as_posix()})：{uses[name]}" for name, path in assets.items()]
    attachments = [f"- [{name}.csv]({name}.csv)" for name in ("table1_convlstm", "table2_auto_labels", "table3_classifier_comparison", "table4_multistation_summary", "table_s1_acceleration_thresholds")]
    return f"""# 藕塘导师展示包摘要

**流程已跑通，负结果完整保留，不再依据现有评价数据调参。** 本包仅机械汇总既有版本化结果，不重训模型、不重算预测，也未使用 Vajont；全部内容均为藕塘科研演示，`formal_warning_output=false`。

## 五张核心图

{chr(10).join(figure_lines)}

## ConvLSTM overall 三折（五种子）

{convlstm}

前两折未超过 persistence；第三折的误差结果仍需结合增量强平滑解释。区间 coverage、width 与 interval score 必须联合报告。

## H=7 自动标签支持

{support_table}

标签是未来位移率与未来正速度 Q90 构成的多点代理结局，不是现场灾害真值。

## 分类器 Panel A：fold-2 all-valid 280 日

{panel_a}

## 分类器 Panel B：fold-2 common 273 日

{panel_b}

固定 NGBoost 未超过严格 persistence；memory 的共同集硬指标与 v1 相同且概率更差；residual 硬指标略有改善，但仍远落后 persistence，概率指标也更差。memory 与 residual 均为 rejected。不报告 fold 3 分类指标。

## v4 多点双轴计数

{v4}

400 个未确认日表示空间支持不足，不是缺测或 green。v4 是透明、非正式基线。

## 证据边界

三种 NGBoost 方案均未证明改善，不能宣称预警有效。分类 SHAP 仅说明独立 NGBoost 的模型依赖，不是 ConvLSTM 内部解释或因果证据。加速度 `A0` 是 fit-only 的项目操作化阈值，不是参考 Word 论文原阈值或正式现场标准。

## CSV 附件

{chr(10).join(attachments)}
"""


def run(config_path: Path = DEFAULT_CONFIG) -> Path:
    config_path = _path(str(config_path)).resolve()
    config = _load_config(config_path)
    paths = {name: _path(value) for name, value in config["inputs"].items()}
    assets = {name: _path(value) for name, value in config.get("display_assets", {}).items()}
    if len(assets) != 5 or any(not path.is_file() for path in assets.values()):
        raise PackageInputError("The five registered display assets must exist")
    for name, path in paths.items():
        if not path.is_file():
            raise PackageInputError(f"Missing registered input {name}: {path}")
    read = {name: pd.read_csv(path) for name, path in paths.items() if path.suffix == ".csv"}
    json_inputs = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in paths.items() if path.suffix == ".json"}
    frames = {
        "table1_convlstm": _table1(read["convlstm_summary"]),
        "table2_auto_labels": _table2(read["auto_label_definition"], read["auto_site_labels"], json_inputs["auto_label_gate"]),
        "table3_classifier_comparison": _table3(read["classifier_metrics"], read["memory_metrics"], json_inputs["memory_manifest"], read["residual_metrics"], json_inputs["residual_manifest"]),
        "table4_multistation_summary": _table4(read["station_timeline"], read["site_timeline"]),
        "table_s1_acceleration_thresholds": _table_s1(read["acceleration_thresholds"]),
    }
    output_dir = _path(config["output_dir"])
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".advisor-ootang-", dir=output_dir.parent) as temporary:
        staging = Path(temporary)
        staged: dict[str, Path] = {}
        for name, frame in frames.items():
            staged[name] = staging / config["outputs"][name]
            frame.to_csv(staged[name], index=False, lineterminator="\n")
        staged["advisor_summary"] = staging / config["outputs"]["advisor_summary"]
        staged["advisor_summary"].write_text(_summary(frames, assets, output_dir), encoding="utf-8")
        manifest = {
            "schema_version": 1, "artifact_kind": "ootang_advisor_research_package",
            "package_status": PACKAGE_STATUS, "case": "ootang", "vajont_used": False,
            "formal_warning_output": False,
            "profile": {"path": str(config_path.relative_to(ROOT)), "sha256": _sha256(config_path)},
            "inputs": {name: {"path": str(path.relative_to(ROOT)), "sha256": _sha256(path)} for name, path in paths.items()},
            "display_assets": {name: {"path": str(path.relative_to(ROOT)), "sha256": _sha256(path)} for name, path in assets.items()},
            "outputs": {},
            "evidence_boundaries": [
                "automatic_labels_are_proxy_outcomes_not_field_hazard_truth",
                "all_ngboost_challengers_failed_registered_improvement_gates",
                "fold3_classifier_metrics_not_reported",
                "v4_is_transparent_nonformal_baseline",
                "acceleration_thresholds_are_project_operationalization",
            ],
        }
        for name, path in staged.items():
            manifest["outputs"][name] = {"path": f"{config['output_dir']}/{path.name}", "sha256": _sha256(path), "rows": len(frames[name]) if name in frames else None}
        staged["manifest"] = staging / config["outputs"]["manifest"]
        staged["manifest"].write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, path in staged.items():
            target = output_dir / path.name
            path.replace(target)
    return output_dir / config["outputs"]["manifest"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    print(run(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
