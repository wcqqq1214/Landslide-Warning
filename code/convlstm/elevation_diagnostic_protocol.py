"""Frozen protocol and atomic artifacts for the 7-channel Ootang diagnostics."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from convlstm import model as base

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / "config" / "ootang_convlstm_elevation_diagnostics.v1.json"
MANIFEST_NAME = "manifest.json"
EXPECTED_CHANNELS = (
    "displacement_idw_grid",
    "elevation_static_idw_grid",
    "RWL",
    "RWL_rate",
    "Rain_cum7",
    "Rain_cum15",
    "Rain_cum30",
)
EXPECTED_RUN_ROOT = Path(
    "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1"
)
EXPECTED_FOLDS = (
    {
        "fold": 1,
        "fit_start_date": "2016-08-06",
        "fit_end_date": "2017-10-31",
        "calibration_start_date": "2017-11-01",
        "calibration_end_date": "2018-02-20",
        "test_start_date": "2018-02-21",
        "test_end_date": "2018-12-04",
    },
    {
        "fold": 2,
        "fit_start_date": "2016-08-06",
        "fit_end_date": "2018-06-17",
        "calibration_start_date": "2018-06-18",
        "calibration_end_date": "2018-12-04",
        "test_start_date": "2018-12-05",
        "test_end_date": "2019-09-17",
    },
    {
        "fold": 3,
        "fit_start_date": "2016-08-06",
        "fit_end_date": "2019-02-02",
        "calibration_start_date": "2019-02-03",
        "calibration_end_date": "2019-09-17",
        "test_start_date": "2019-09-18",
        "test_end_date": "2020-06-30",
    },
)


def file_sha256(path):
    """Return a streaming SHA-256 digest for one file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_protocol():
    if not PROTOCOL_PATH.is_file():
        raise RuntimeError(f"冻结协议不存在: {PROTOCOL_PATH}")
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def validate_protocol(protocol):
    """Reject any silent drift from the pre-run 7-channel fixed-120 design."""
    expected_values = {
        ("schema_version",): "ootang_convlstm_elevation_diagnostics_protocol_v1",
        ("protocol_id",): "ootang-convlstm-elevation-fixed120-v1",
        ("protocol_version",): "1.0",
        ("status",): "frozen_for_internal_exploratory_diagnostic",
        ("case",): "ootang",
        ("formal_warning_output",): False,
        ("vajont_used",): False,
        ("source_data_status",):
            "materialized_daily_modeling_series_not_independent_raw_gnss",
        ("model_input", "schema"): "displacement_elevation_exog_v1",
        ("model_input", "channel_count"): 7,
        ("model_input", "elevation_method"):
            "station_zscore_then_horizontal_idw_static_channel",
        ("model_input", "elevation_in_three_dimensional_distance"): False,
        ("training", "lookback_days"): 7,
        ("training", "horizon_days"): 1,
        ("training", "hidden_channels"): 16,
        ("training", "kernel_size"): 3,
        ("training", "epochs"): 120,
        ("training", "learning_rate"): 0.001,
        ("training", "loss"): "mean_pinball_loss",
        ("training", "target_coverage"): 0.8,
        ("training", "calibration_fraction_within_train"): 0.2,
        ("training", "hyperparameter_tuning"): False,
        ("outer_validation", "method"):
            "expanding_window_nonoverlapping_fixed_287d_test",
        ("outer_validation", "fold_count"): 3,
        ("outer_validation", "test_windows_per_fold"): 287,
        ("outer_validation", "minimum_fit_windows"): 365,
        ("outer_validation", "same_date_all_stations_grouped"): True,
        ("outer_validation", "test_used_for_selection"): False,
        ("rolling_validation", "seed"): 0,
        ("rolling_validation", "run_id"): "rolling_seed0",
        ("seed_stability", "seeds"): [0, 1, 2, 3, 4],
        ("seed_stability", "run_count"): 15,
        ("seed_stability", "best_seed_selected"): False,
        ("seed_stability", "run_id"): "seed_stability_0_4",
        ("seed_stability", "save_all_predictions"): True,
        ("seed_stability", "rolling_seed0_reproduction_required"): True,
        ("next_stage_policy", "inner_validation_rerun_now"): False,
        ("next_stage_policy", "capacity_sensitivity_rerun_now"): False,
    }
    for keys, expected in expected_values.items():
        observed = protocol
        try:
            for key in keys:
                observed = observed[key]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"冻结协议缺少字段: {'.'.join(keys)}") from exc
        if observed != expected:
            raise RuntimeError(
                f"冻结协议字段 {'.'.join(keys)} 漂移: {observed!r} != {expected!r}"
            )

    if tuple(protocol["model_input"]["channels"]) != EXPECTED_CHANNELS:
        raise RuntimeError("冻结协议的 7 通道名称或顺序与当前模型不一致")
    if protocol["training"]["quantiles"] != [0.1, 0.5, 0.9]:
        raise RuntimeError("冻结协议分位数与当前模型不一致")
    model_settings = (
        base.MODEL_INPUT_SCHEMA,
        base.MODEL_INPUT_CHANNELS,
        tuple(base.EXOG_COLS),
        base.LOOKBACK,
        base.HORIZON,
        base.HIDDEN,
        base.KERNEL,
        base.EPOCHS,
        base.LR,
        tuple(base.QUANTILES),
        base.TARGET_COVERAGE,
        base.CAL_FRAC,
        base.SEED,
    )
    expected_model_settings = (
        "displacement_elevation_exog_v1",
        7,
        EXPECTED_CHANNELS[2:],
        7,
        1,
        16,
        3,
        120,
        0.001,
        (0.1, 0.5, 0.9),
        0.8,
        0.2,
        0,
    )
    if model_settings != expected_model_settings:
        raise RuntimeError("当前 ConvLSTM 常量已偏离 fixed120_v1 冻结设置")
    if protocol["seed_stability"]["run_count"] != (
        protocol["outer_validation"]["fold_count"]
        * len(protocol["seed_stability"]["seeds"])
    ):
        raise RuntimeError("冻结协议的种子-折运行总数不一致")

    observed_folds = tuple(protocol["outer_validation"].get("folds", ()))
    if observed_folds != EXPECTED_FOLDS:
        raise RuntimeError("冻结协议的三折日期边界已偏离 fixed120_v1")

    run_root = Path(protocol["output_contract"]["run_root"])
    if run_root != EXPECTED_RUN_ROOT:
        raise RuntimeError("冻结协议的输出目录已偏离 fixed120_v1")
    if not protocol["output_contract"]["historical_six_channel_root_files_preserved"]:
        raise RuntimeError("冻结协议不得覆盖历史 6 通道根目录产物")
    if not protocol["output_contract"]["stage_bundles_promoted_independently"]:
        raise RuntimeError("冻结协议必须启用独立阶段整包提升")
    historical = protocol["output_contract"].get(
        "historical_six_channel_sha256",
        {},
    )
    if not historical:
        raise RuntimeError("冻结协议必须记录历史 6 通道产物哈希")
    for relative_path, expected_sha256 in historical.items():
        artifact = ROOT / relative_path
        if not artifact.is_file() or file_sha256(artifact) != expected_sha256:
            raise RuntimeError(f"历史 6 通道产物已变化: {relative_path}")
    return protocol


def load_and_validate_protocol():
    """Load the authoritative protocol and verify it against model constants."""
    return validate_protocol(_read_protocol())


FROZEN_PROTOCOL = load_and_validate_protocol()
RUN_ROOT = ROOT / FROZEN_PROTOCOL["output_contract"]["run_root"]
ROLLING_DIR = RUN_ROOT / FROZEN_PROTOCOL["rolling_validation"]["run_id"]
SEED_DIR = RUN_ROOT / FROZEN_PROTOCOL["seed_stability"]["run_id"]
INNER_DIR = RUN_ROOT / "inner_validation_v1"
CAPACITY_DIR = RUN_ROOT / "capacity_sensitivity_v1"


def _relative_or_absolute(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _git_value(*args):
    try:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _artifact_record(path, *, frame=None):
    path = Path(path)
    record = {
        "path": _relative_or_absolute(path),
        "size_bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }
    if frame is not None:
        record.update({
            "rows": len(frame),
            "columns": list(frame.columns),
        })
    return record


def _write_dataframe_csv(frame, path):
    """Write one DataFrame; isolated for failure-injection contract tests."""
    frame.to_csv(path, index=False)


def _build_stage_manifest(
    *,
    stage,
    stage_parameters,
    source_paths,
    output_records,
):
    protocol = load_and_validate_protocol()
    input_paths = (base.FEAT_CSV, base.COORD_CSV, PROTOCOL_PATH)
    tracked_status = _git_value("status", "--porcelain", "--untracked-files=no")
    return {
        "schema_version": "ootang_convlstm_stage_bundle_manifest_v1",
        "status": "completed",
        "stage": stage,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_scope": "internal_exploratory_materialized_daily_series",
        "formal_warning_output": False,
        "confirmatory_external_validation": False,
        "protocol": {
            "id": protocol["protocol_id"],
            "version": protocol["protocol_version"],
            "path": _relative_or_absolute(PROTOCOL_PATH),
            "sha256": file_sha256(PROTOCOL_PATH),
            "frozen_content": protocol,
        },
        "model_input": {
            "schema": base.MODEL_INPUT_SCHEMA,
            "channel_count": base.MODEL_INPUT_CHANNELS,
            "channels": list(EXPECTED_CHANNELS),
            "elevation_method": protocol["model_input"]["elevation_method"],
        },
        "inputs": [_artifact_record(path) for path in input_paths],
        "sources": [_artifact_record(path) for path in source_paths],
        "parameters": stage_parameters,
        "runtime": {
            "python": platform.python_version(),
            "python_executable": sys.executable,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "torch": torch.__version__,
        },
        "git": {
            "commit": _git_value("rev-parse", "HEAD"),
            "tracked_worktree_dirty": (
                None if tracked_status is None else bool(tracked_status)
            ),
        },
        "outputs": output_records,
    }


def _recover_interrupted_promotion(target_dir):
    """Restore the newest backup if a prior promotion lost its target."""
    target_dir = Path(target_dir)
    backups = list(target_dir.parent.glob(f".{target_dir.name}.backup-*"))
    if target_dir.exists():
        for backup in backups:
            shutil.rmtree(backup)
        return
    valid_backups = []
    for backup in backups:
        manifest_path = backup / MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        output_records = manifest.get("outputs", [])
        if manifest.get("status") != "completed" or not output_records:
            continue
        output_names = [
            Path(record.get("path", "")).name
            for record in output_records
        ]
        if (
            len(set(output_names)) != len(output_names)
            or any(not name for name in output_names)
        ):
            continue
        if all(
            (backup / name).is_file()
            and record.get("sha256") == file_sha256(backup / name)
            for name, record in zip(output_names, output_records, strict=True)
        ):
            valid_backups.append(backup)
    if not valid_backups:
        if backups:
            raise RuntimeError("检测到损坏的阶段备份，拒绝自动恢复")
        return
    newest = max(valid_backups, key=lambda path: path.stat().st_mtime_ns)
    newest.rename(target_dir)
    for backup in backups:
        if backup != newest and backup.exists():
            shutil.rmtree(backup)


def write_stage_bundle(
    target_dir,
    frames,
    *,
    stage,
    stage_parameters,
    source_paths,
):
    """Write, hash and promote a complete multi-file stage bundle.

    An existing complete target is retained until every replacement CSV and its
    manifest have been written successfully. Promotion failures restore the
    previous target when possible.
    """
    target_dir = Path(target_dir)
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    _recover_interrupted_promotion(target_dir)
    token = uuid.uuid4().hex
    staging = target_dir.parent / f".{target_dir.name}.staging-{token}"
    backup = target_dir.parent / f".{target_dir.name}.backup-{token}"
    staging.mkdir()
    try:
        output_records = []
        for filename, frame in frames.items():
            relative = Path(filename)
            if relative.name != filename or relative.suffix != ".csv":
                raise ValueError(f"阶段产物必须是扁平 CSV 文件名: {filename}")
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                raise ValueError(f"阶段产物必须是非空 DataFrame: {filename}")
            output_path = staging / filename
            _write_dataframe_csv(frame, output_path)
            record = _artifact_record(output_path, frame=frame)
            record["path"] = _relative_or_absolute(target_dir / filename)
            output_records.append(record)

        manifest = _build_stage_manifest(
            stage=stage,
            stage_parameters=stage_parameters,
            source_paths=tuple(Path(path) for path in source_paths),
            output_records=output_records,
        )
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        if target_dir.exists():
            target_dir.rename(backup)
        try:
            staging.rename(target_dir)
        except Exception:
            if backup.exists() and not target_dir.exists():
                backup.rename(target_dir)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return target_dir / MANIFEST_NAME


def validate_stage_manifest(
    manifest_path,
    *,
    expected_stage,
    required_outputs=(),
):
    """Verify a stage bundle before it is consumed by a downstream analysis."""
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "completed" or manifest.get("stage") != expected_stage:
        raise RuntimeError("上游阶段 manifest 状态或阶段名称不一致")
    if manifest.get("protocol", {}).get("sha256") != file_sha256(PROTOCOL_PATH):
        raise RuntimeError("上游阶段协议哈希与当前冻结协议不一致")
    model_input = manifest.get("model_input", {})
    if (
        model_input.get("schema") != base.MODEL_INPUT_SCHEMA
        or model_input.get("channel_count") != base.MODEL_INPUT_CHANNELS
        or tuple(model_input.get("channels", ())) != EXPECTED_CHANNELS
    ):
        raise RuntimeError("上游阶段模型输入 schema 与当前 7 通道模型不一致")

    input_by_path = {record["path"]: record for record in manifest.get("inputs", [])}
    for path in (base.FEAT_CSV, base.COORD_CSV):
        key = _relative_or_absolute(path)
        if input_by_path.get(key, {}).get("sha256") != file_sha256(path):
            raise RuntimeError(f"上游阶段输入哈希已变化: {key}")

    source_records = manifest.get("sources", [])
    if not source_records:
        raise RuntimeError("上游阶段 manifest 缺少源码哈希")
    for record in source_records:
        source_path = ROOT / record["path"]
        if (
            not source_path.is_file()
            or record.get("sha256") != file_sha256(source_path)
        ):
            raise RuntimeError(f"上游阶段源码哈希已变化: {record['path']}")

    output_by_name = {
        Path(record["path"]).name: record
        for record in manifest.get("outputs", [])
    }
    for filename in required_outputs:
        output_path = manifest_path.parent / filename
        record = output_by_name.get(filename)
        if record is None or not output_path.is_file():
            raise RuntimeError(f"上游阶段缺少声明产物: {filename}")
        if record.get("sha256") != file_sha256(output_path):
            raise RuntimeError(f"上游阶段产物哈希不一致: {filename}")
    return manifest


def require_next_stage_enabled(policy_key):
    """Fail closed for follow-up experiments frozen out of protocol v1."""
    protocol = load_and_validate_protocol()
    if protocol["next_stage_policy"].get(policy_key) is not True:
        raise RuntimeError(f"冻结协议未授权执行后续阶段: {policy_key}")
