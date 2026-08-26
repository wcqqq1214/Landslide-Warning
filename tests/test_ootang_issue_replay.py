"""Fail-closed contracts for runner-independent pending-issue replay."""

from __future__ import annotations

import ast
import copy
from datetime import date, datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd
import torch


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from convlstm import ootang_production_bundle as producer_bundle  # noqa: E402
from monitoring import ootang_issue_replay as replay  # noqa: E402
from monitoring import ootang_live_source as source_authority  # noqa: E402


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class IndependentIssueReplayContracts(unittest.TestCase):
    """Build actual safe checkpoints and attack each verifier boundary."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="ootang-issue-replay-test-", dir=ROOT
        )
        self.root = Path(self._temporary.name)
        self.runtime = self.root / "runtime"
        self.profile = replay.load_replay_profile()
        self.paths = replay.runtime_paths(self.profile, self.runtime)
        self.paths.objects.mkdir(parents=True)
        self.frame = self._activation_frame()
        (
            self.activation,
            self.activation_payload,
            self.prerequisites,
            self.training_source,
        ) = self._activation_lineage()
        self.preprocessing = replay._activation_preprocessing(
            activation=self.activation,
            activation_payload=self.activation_payload,
            training_source=self.training_source,
            prerequisite_data_manifest=self.prerequisites.source.data_manifest,
            paths=self.paths,
            profile=self.profile,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _canonical(self, payload: object) -> bytes:
        return replay._canonical_bytes(payload, newline=True)

    def _object(self, payload: object, suffix: str) -> replay.Artifact:
        raw = payload if isinstance(payload, bytes) else self._canonical(payload)
        digest = _sha256(raw)
        path = self.paths.objects / f"{digest}{suffix}"
        path.write_bytes(raw)
        return replay.Artifact(path.resolve(), digest, len(raw))

    def _activation_frame(self) -> pd.DataFrame:
        count = 373
        dates = pd.date_range("2020-07-01", periods=count, freq="D")
        steps = np.arange(count, dtype=np.float64)
        frame = pd.DataFrame({"Date": dates.strftime("%Y-%m-%d")})
        for index, column in enumerate(
            self.profile["verification"]["displacement_columns"]
        ):
            frame[column] = (
                100.0 * (index + 1)
                + 0.08 * (index + 1) * steps
                + 0.4 * np.sin(steps / (11.0 + index))
            )
        rain = 4.0 + 3.0 * (np.sin(steps / 5.0) + 1.0)
        frame["RWL"] = 152.0 + 1.7 * np.sin(steps / 23.0)
        frame["RWL_rate"] = np.diff(
            frame["RWL"], prepend=frame["RWL"].iloc[0]
        )
        frame["Rain"] = rain
        for window in (7, 15, 30):
            frame[f"Rain_cum{window}"] = (
                pd.Series(rain).rolling(window, min_periods=1).sum().to_numpy()
            )
        contract = self.profile["_deploy_payload"]["source_feed"]
        model_to_column = dict(
            zip(
                contract["station_order_model"],
                self.profile["verification"]["displacement_columns"],
                strict=True,
            )
        )
        records: list[dict[str, object]] = []
        for index, day in enumerate(dates):
            date_text = day.date().isoformat()
            records.append(
                {
                    "schema_version": contract["record_schema_version"],
                    "date": date_text,
                    "revision_id": f"revision-{date_text}",
                    "observed_at_utc": f"{date_text}T08:00:00Z",
                    "available_at_utc": f"{date_text}T08:01:00Z",
                    "finalized_at_utc": f"{date_text}T08:02:00Z",
                    "finalized": True,
                    contract["raw_fields"]["rainfall"]: float(rain[index]),
                    contract["raw_fields"]["reservoir_water_level"]: float(
                        frame["RWL"].iloc[index]
                    ),
                    contract["raw_fields"]["displacement"]: {
                        station: float(frame[model_to_column[station]].iloc[index])
                        for station in contract["station_order_live"]
                    },
                }
            )
        feed_payload = {
            "schema_version": contract["schema_version"],
            "outcome_source_id": "unit-test-survey-source",
            "exported_at_utc": "2021-07-08T09:00:00Z",
            "records": records,
        }
        self.feed_raw = replay._canonical_bytes(feed_payload, newline=True)
        self.feed = source_authority._parse_feed(
            self.feed_raw, self.profile["_deploy_payload"], now=None
        )
        historical, _ = source_authority._load_historical(
            self.profile["_deploy_payload"], project_root=ROOT
        )
        return source_authority._combine_source(
            historical, self.feed, self.profile["_deploy_payload"]
        )

    def _activation_lineage(
        self,
    ) -> tuple[replay.Artifact, dict[str, object], object, dict[str, object]]:
        watermark = date.fromisoformat(str(self.frame["Date"].iloc[-1]))
        dataset = self._object(
            {
                "schema_version": "ootang_canonical_model_source_v1",
                "case": "ootang",
                "date_timezone": self.profile["_deploy_payload"]["source_feed"][
                    "date_timezone"
                ],
                "columns": list(self.frame.columns),
                "maximum_complete_finalized_date": watermark.isoformat(),
                "rows": [
                    [values[0], *[float(value) for value in values[1:]]]
                    for values in self.frame.itertuples(index=False, name=None)
                ],
            },
            ".source.json",
        )
        feed = self._object(self.feed_raw, ".feed.json")
        historical_contract = self.profile["_deploy_payload"]["historical_base"]
        historical_path = ROOT / historical_contract["path"]
        historical_raw = historical_path.read_bytes()
        source_contract = self.profile["_deploy_payload"]["source_feed"]
        semantic = self._object(
            {
                "schema_version": "ootang_source_semantic_manifest_v1",
                "case": "ootang",
                "outcome_source_id": "unit-test-survey-source",
                "maximum_complete_finalized_date": watermark.isoformat(),
                "canonical_dataset": dataset.as_dict(),
                "lineage": {
                    "historical_base": {
                        "path": str(historical_path.resolve()),
                        "sha256": _sha256(historical_raw),
                        "size_bytes": len(historical_raw),
                        "rows": historical_contract["rows"],
                        "last_date": historical_contract["last_date"],
                        "role": historical_contract["role"],
                    },
                    "daily_feed_snapshot": {
                        **feed.as_dict(),
                        "schema_version": source_contract["schema_version"],
                        "exported_at_utc": self.feed.exported_at_text,
                        "first_date": self.feed.records[0].day.isoformat(),
                        "last_date": watermark.isoformat(),
                        "rows": len(self.feed.records),
                        "date_revision_pairs_sha256": _sha256(
                            replay._canonical_bytes(
                                [
                                    [record.day.isoformat(), record.revision_id]
                                    for record in self.feed.records
                                ],
                                newline=True,
                            )
                        ),
                    },
                },
                "semantics": {
                    "date_timezone": source_contract["date_timezone"],
                    "recorded_time_timezone": source_contract[
                        "recorded_time_timezone"
                    ],
                    "frequency": source_contract["expected_frequency"],
                    "station_order_live": source_contract["station_order_live"],
                    "station_order_model": source_contract["station_order_model"],
                    "model_columns": list(self.frame.columns),
                    "units": source_contract["units"],
                    "derived_features": source_contract["derived_features"],
                    "external_derived_columns_accepted": False,
                    "all_records_finalized": True,
                    "daily_continuity_verified": True,
                    "finite_values_verified": True,
                },
                "dataset_summary": {
                    "rows": len(self.frame),
                    "first_date": str(self.frame.iloc[0]["Date"]),
                    "last_date": watermark.isoformat(),
                },
            },
            ".source-manifest.json",
        )
        captured = self.feed.exported_at_text
        latest = {
            station: float(
                self.frame[
                    self.profile["verification"]["displacement_columns"][
                        self.profile["verification"]["station_order_model"].index(
                            station
                        )
                    ]
                ].iloc[-1]
            )
            for station in self.profile["verification"]["station_order_live"]
        }
        activation_payload: dict[str, object] = {
            "schema_version": "ootang_live_source_snapshot_v1",
            "case": "ootang",
            "captured_at_utc": captured,
            "maximum_complete_finalized_date": watermark.isoformat(),
            "stations": self.profile["verification"]["station_order_live"],
            "outcome_source_id": "unit-test-survey-source",
            "data_manifest": semantic.as_dict(),
            "latest_finalized_displacement_mm": latest,
        }
        raw = self._canonical(activation_payload)
        self.paths.activation_manifest.parent.mkdir(parents=True, exist_ok=True)
        self.paths.activation_manifest.write_bytes(raw)
        activation = replay.Artifact(
            self.paths.activation_manifest.resolve(), _sha256(raw), len(raw)
        )
        prerequisites = SimpleNamespace(
            source=SimpleNamespace(
                data_manifest=SimpleNamespace(
                    path=semantic.path,
                    sha256=semantic.sha256,
                    size_bytes=semantic.size_bytes,
                )
            )
        )
        source = {
            "training_cutoff_date": watermark.isoformat(),
            "activation_captured_at_utc": captured,
            "activation_source_manifest_sha256": activation.sha256,
            "dataset_sha256": dataset.sha256,
            "semantic_manifest_sha256": semantic.sha256,
        }
        return activation, activation_payload, prerequisites, source

    def _checkpoint_payload(self, seed: int) -> dict[str, object]:
        contract = self.profile["verification"]
        torch.manual_seed(seed)
        model = replay._IndependentConvLSTMForecast(
            contract["input_channels"],
            contract["hidden_channels"],
            contract["kernel_size"],
        )
        _, xy, elevation = replay._load_geometry(self.profile)
        grid_x, grid_y = replay._grid(
            xy, height=contract["grid_height"], width=contract["grid_width"]
        )
        interpolation = replay._interpolation_weights(xy, grid_x, grid_y)
        elevation_grid = (
            ((elevation - elevation.mean()) / elevation.std()) @ interpolation.T
        ).reshape(contract["grid_height"], contract["grid_width"])
        state = {
            key: value.detach().clone().contiguous().cpu()
            for key, value in model.state_dict().items()
        }
        tensor = lambda value: torch.as_tensor(  # noqa: E731
            np.asarray(value), dtype=torch.float64
        ).clone().contiguous()
        return {
            "schema_version": contract["checkpoint_schema_version"],
            "checkpoint_format": contract["checkpoint_format"],
            "case": "ootang",
            "seed": seed,
            "source": dict(self.training_source),
            "architecture": {
                "model_class": "ConvLSTMForecast",
                "input_channels": contract["input_channels"],
                "hidden_channels": contract["hidden_channels"],
                "kernel_size": contract["kernel_size"],
                "quantiles": contract["quantiles"],
                "lookback_days": contract["lookback_days"],
                "horizon_days": contract["horizon_days"],
                "grid_h": contract["grid_height"],
                "grid_w": contract["grid_width"],
            },
            "feature_schema": {
                "input_schema": contract["input_schema"],
                "displacement_columns": contract["displacement_columns"],
                "exogenous_columns": contract["exogenous_columns"],
                "static_spatial_columns": contract["static_spatial_columns"],
                "stations_model": contract["station_order_model"],
                "station_geometry_sha256": self.profile["_geometry"]["sha256"],
            },
            "normalization": {
                "displacement_mean": tensor(
                    self.preprocessing.displacement_mean
                ),
                "displacement_scale": tensor(
                    self.preprocessing.displacement_scale
                ),
                "exogenous_mean": tensor(self.preprocessing.exogenous_mean),
                "exogenous_scale": tensor(self.preprocessing.exogenous_scale),
                "delta_scale": tensor(self.preprocessing.delta_scale),
                "elevation_mean_m": float(elevation.mean()),
                "elevation_scale_m": float(elevation.std()),
            },
            "spatial": {
                "elevation_grid": torch.as_tensor(
                    elevation_grid, dtype=torch.float32
                ).clone().contiguous(),
                "readout_weights": torch.as_tensor(
                    replay._station_readout_weights(xy, grid_x, grid_y),
                    dtype=torch.float32,
                ).clone().contiguous(),
            },
            "state_dict": state,
        }

    def _safe_checkpoint(
        self, seed: int, payload: dict[str, object] | None = None
    ) -> tuple[replay.Artifact, bytes, replay._Checkpoint]:
        observed = self._checkpoint_payload(seed) if payload is None else payload
        buffer = io.BytesIO()
        torch.save(observed, buffer)
        raw = buffer.getvalue()
        artifact = self._object(raw, "")
        loaded = replay._load_checkpoint(
            artifact,
            raw,
            expected_seed=seed,
            expected_source=self.training_source,
            expected_preprocessing=self.preprocessing,
            profile=self.profile,
        )
        return artifact, raw, loaded

    def _recent_rows(self) -> list[dict[str, object]]:
        columns = [
            *self.profile["verification"]["displacement_columns"],
            *self.profile["verification"]["exogenous_columns"],
        ]
        return [
            {"date": str(row["Date"]), **{key: float(row[key]) for key in columns}}
            for _, row in self.frame.tail(7).iterrows()
        ]

    def _issue_implementation_record(self) -> dict[str, object]:
        def static_artifact(path: Path) -> dict[str, object]:
            raw = path.read_bytes()
            return {
                "path": str(path.resolve()),
                "sha256": _sha256(raw),
                "size_bytes": len(raw),
            }

        return {
            "schema_version": "ootang_issue_producer_implementation_v1",
            "deploy_profile": static_artifact(Path(self.profile["_deploy_path"])),
            "issue_producer": static_artifact(
                ROOT / "code/monitoring/ootang_issue_producer.py"
            ),
            "pyproject": static_artifact(ROOT / "pyproject.toml"),
            "uv_lock": static_artifact(ROOT / "uv.lock"),
            "runtime": replay._runtime_environment(),
        }

    def _verified_inputs(self) -> replay._VerifiedInputs:
        source, projection, prerequisites, issue_path = self._ready_materials()
        return replay._verify_materials(
            profile=self.profile,
            paths=self.paths,
            source=source,
            projection=projection,
            prerequisites=prerequisites,
            issue_path=issue_path,
            now=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
        )

        # Kept below as explicit documentation of the minimal receipt shape.
        contract = self.profile["verification"]
        target = date(2021, 7, 9)
        training = self._object(b'{"fixture":"training"}\n', "")
        checkpoints = [
            {
                "seed": seed,
                "artifact": self._object(
                    f"safe-checkpoint-{seed}\n".encode(), ""
                ).as_dict(),
            }
            for seed in contract["seeds"]
        ]
        model_version = "fixture-five-seed-model"
        outer_payload = {
            "model_version": model_version,
            "input_schema_sha256": replay._input_schema_sha256(self.profile),
            "training_manifest": training.as_dict(),
            "checkpoints": checkpoints,
        }
        outer_raw = self._canonical(outer_payload)
        self.paths.model_manifest.parent.mkdir(parents=True, exist_ok=True)
        self.paths.model_manifest.write_bytes(outer_raw)
        outer = replay.Artifact(
            self.paths.model_manifest.resolve(), _sha256(outer_raw), len(outer_raw)
        )

        implementation_record = self._issue_implementation_record()
        normalized_implementation = replay._validate_issue_implementation(
            implementation_record, profile=self.profile
        )
        rows = self._recent_rows()
        latest = rows[-1]
        stations: list[dict[str, object]] = []
        for station in contract["station_order_live"]:
            model_index = contract["station_order_model"].index(station)
            persistence = float(
                latest[contract["displacement_columns"][model_index]]
            )
            stations.append(
                {
                    "station": station,
                    "persistence_mm": persistence,
                    **{
                        f"seed{seed}_p50_mm": persistence
                        for seed in contract["seeds"]
                    },
                }
            )
        input_payload: dict[str, object] = {
            "schema_version": contract["input_manifest_schema_version"],
            "case": "ootang",
            "target_date": target.isoformat(),
            "source_watermark": "2021-07-08",
            "scientific_semantics_sha256": "0" * 64,
            "source": {
                "canonical_dataset": self.preprocessing.canonical_dataset.as_dict(),
                "semantic_manifest": self.preprocessing.semantic_manifest.as_dict(),
                "activation_source_manifest": self.activation.as_dict(),
            },
            "model": {
                "outer_manifest": outer.as_dict(),
                "training_manifest": training.as_dict(),
                "checkpoints": checkpoints,
            },
            "implementation": implementation_record,
            "model_rows": rows,
            "station_order_model": contract["station_order_model"],
            "station_order_live": contract["station_order_live"],
            "expert_order": [
                "persistence",
                "seed0_p50",
                "seed1_p50",
                "seed2_p50",
                "seed3_p50",
                "seed4_p50",
            ],
        }
        input_payload["scientific_semantics_sha256"] = replay._canonical_sha256(
            replay._scientific_semantics(
                manifest=input_payload,
                issue_stations=stations,
                implementation=normalized_implementation,
            )
        )
        input_manifest = self._object(input_payload, ".json")
        issue_payload = {
            "schema_version": contract["issue_schema_version"],
            "target_date": target.isoformat(),
            "generated_at_utc": "2021-07-08T10:00:00Z",
            "source_as_of_at_utc": "2021-07-08T09:30:00Z",
            "source_snapshot_sha256": self.activation.sha256,
            "model_manifest_sha256": outer.sha256,
            "input_manifest": input_manifest.as_dict(),
            "stations": stations,
        }
        issue_raw = self._canonical(issue_payload)
        issue_path = self.paths.issue_inbox / f"{target.isoformat()}.json"
        issue_path.parent.mkdir(parents=True, exist_ok=True)
        issue_path.write_bytes(issue_raw)
        issue = replay.Artifact(issue_path.resolve(), _sha256(issue_raw), len(issue_raw))
        comparisons = tuple(
            {
                "station": station_row["station"],
                "seed": seed,
                "issued_p50_mm": station_row[f"seed{seed}_p50_mm"],
                "replayed_p50_mm": station_row[f"seed{seed}_p50_mm"],
                "absolute_difference_mm": 0.0,
            }
            for station_row in stations
            for seed in contract["seeds"]
        )
        replay_projection = [
            {
                "station": station_row["station"],
                **{
                    f"seed{seed}_p50_mm": station_row[f"seed{seed}_p50_mm"]
                    for seed in contract["seeds"]
                },
            }
            for station_row in stations
        ]
        return replay._VerifiedInputs(
            target=target,
            issue=issue,
            input_manifest=input_manifest,
            source={
                "source_watermark": "2021-07-08",
                "canonical_dataset": self.preprocessing.canonical_dataset.as_dict(),
                "semantic_manifest": self.preprocessing.semantic_manifest.as_dict(),
                "activation_source_manifest": self.activation.as_dict(),
                "activation_semantic_manifest": (
                    self.preprocessing.semantic_manifest.as_dict()
                ),
                "activation_canonical_dataset": (
                    self.preprocessing.canonical_dataset.as_dict()
                ),
            },
            model={
                "outer_manifest": outer.as_dict(),
                "training_manifest": training.as_dict(),
                "checkpoints": checkpoints,
                "model_version": model_version,
                "input_schema_sha256": replay._input_schema_sha256(self.profile),
            },
            input_rows_sha256=replay._canonical_sha256(rows),
            replayed_predictions_sha256=replay._canonical_sha256(replay_projection),
            persistence_values_verified=8,
            p50_values_verified=40,
            maximum_abs_difference_mm=0.0,
            comparisons=comparisons,
        )

    def _ready_materials(self) -> tuple[object, object, object, Path]:
        """Materialize every exact schema consumed by ``_verify_materials``."""

        contract = self.profile["verification"]
        deploy_model = self.profile["_deploy_payload"]["model"]
        checkpoint_rows: list[dict[str, object]] = []
        loaded_checkpoints: list[replay._Checkpoint] = []
        for seed in contract["seeds"]:
            artifact, _, loaded = self._safe_checkpoint(seed)
            checkpoint_rows.append({"seed": seed, "artifact": artifact.as_dict()})
            loaded_checkpoints.append(loaded)
        training_windows = (
            len(self.frame) - contract["lookback_days"] - contract["horizon_days"] + 1
        )
        identity = replay._canonical_sha256(
            {
                "schema_version": "ootang_five_seed_bundle_identity_v1",
                "source_bindings": dict(self.training_source),
                "input_schema_sha256": replay._input_schema_sha256(self.profile),
                "fit_policy": deploy_model["fit_policy"],
                "seeds": contract["seeds"],
                "epochs": deploy_model["epochs"],
                "training_windows": training_windows,
                "checkpoints": [
                    {
                        "seed": row["seed"],
                        "sha256": row["artifact"]["sha256"],
                        "size_bytes": row["artifact"]["size_bytes"],
                    }
                    for row in checkpoint_rows
                ],
            }
        )
        model_version = f"{deploy_model['model_version_prefix']}-{identity[:16]}"
        created_at = "2021-07-08T09:00:00Z"

        def file_hash(path: Path) -> str:
            return _sha256(path.read_bytes())

        training_payload = {
            "schema_version": contract["training_manifest_schema_version"],
            "case": "ootang",
            "model_version": model_version,
            "created_at_utc": created_at,
            "training_cutoff_date": self.training_source["training_cutoff_date"],
            "source_bindings": dict(self.training_source),
            "input_schema_sha256": replay._input_schema_sha256(self.profile),
            "training_policy": {
                "fit_policy": deploy_model["fit_policy"],
                "online_calibration_policy": deploy_model[
                    "online_calibration_policy"
                ],
                "device": "cpu",
                "deterministic_algorithms": True,
                "torch_threads": 1,
                "epochs": deploy_model["epochs"],
                "learning_rate": deploy_model["learning_rate"],
                "holdout_windows": 0,
                "best_seed_selected": False,
                "mkldnn_enabled": False,
                "test_epoch_override": False,
            },
            "stations": {
                "live": contract["station_order_live"],
                "model": contract["station_order_model"],
            },
            "seeds": contract["seeds"],
            "best_seed_selected": False,
            "training_windows": training_windows,
            "seed_training": [
                {
                    "seed": seed,
                    "epochs": deploy_model["epochs"],
                    "training_windows": training_windows,
                    "pinball_loss": [1.0] * deploy_model["epochs"],
                }
                for seed in contract["seeds"]
            ],
            "checkpoints": checkpoint_rows,
            "reload_replay": [
                {
                    "seed": seed,
                    "in_memory_p50_mm": [0.0] * 8,
                    "reloaded_p50_mm": [0.0] * 8,
                    "max_abs_difference_mm": 0.0,
                    "absolute_tolerance_mm": contract[
                        "prediction_absolute_tolerance_mm"
                    ],
                    "equivalent": True,
                }
                for seed in contract["seeds"]
            ],
            "implementation": {
                "deploy_profile_sha256": self.profile["_deploy_sha256"],
                "base_model_sha256": file_hash(ROOT / "code/convlstm/model.py"),
                "producer_sha256": file_hash(
                    ROOT / "code/convlstm/ootang_production_bundle.py"
                ),
                "pyproject_sha256": self.profile["_pyproject"]["sha256"],
                "uv_lock_sha256": self.profile["_uv_lock"]["sha256"],
                "torch_version": str(torch.__version__),
                "numpy_version": str(np.__version__),
            },
        }
        training = self._object(training_payload, "")
        outer_payload = {
            "schema_version": contract["outer_manifest_schema_version"],
            "case": "ootang",
            "model_version": model_version,
            "created_at_utc": created_at,
            "training_cutoff_date": self.training_source["training_cutoff_date"],
            "stations": contract["station_order_live"],
            "seeds": contract["seeds"],
            "best_seed_selected": False,
            "target": {
                "name": "next_natural_day_cumulative_displacement",
                "unit": "mm",
                "horizon": "P1D",
            },
            "input_schema_sha256": replay._input_schema_sha256(self.profile),
            "training_manifest": training.as_dict(),
            "checkpoints": checkpoint_rows,
        }
        outer_raw = self._canonical(outer_payload)
        self.paths.model_manifest.parent.mkdir(parents=True, exist_ok=True)
        self.paths.model_manifest.write_bytes(outer_raw)
        outer = replay.Artifact(
            self.paths.model_manifest.resolve(), _sha256(outer_raw), len(outer_raw)
        )

        replay._configure_inference(self.profile)
        recent_rows = self._recent_rows()
        predictions = {
            checkpoint.seed: replay._predict_checkpoint(
                checkpoint, recent_rows, self.profile
            )
            for checkpoint in loaded_checkpoints
        }
        latest = recent_rows[-1]
        station_rows: list[dict[str, object]] = []
        for station in contract["station_order_live"]:
            model_index = contract["station_order_model"].index(station)
            station_rows.append(
                {
                    "station": station,
                    "persistence_mm": float(
                        latest[contract["displacement_columns"][model_index]]
                    ),
                    **{
                        f"seed{seed}_p50_mm": predictions[seed][station]
                        for seed in contract["seeds"]
                    },
                }
            )
        implementation_record = self._issue_implementation_record()
        normalized_implementation = replay._validate_issue_implementation(
            implementation_record, profile=self.profile
        )
        target = date(2021, 7, 9)
        input_payload: dict[str, object] = {
            "schema_version": contract["input_manifest_schema_version"],
            "case": "ootang",
            "target_date": target.isoformat(),
            "source_watermark": "2021-07-08",
            "scientific_semantics_sha256": "0" * 64,
            "source": {
                "canonical_dataset": self.preprocessing.canonical_dataset.as_dict(),
                "semantic_manifest": self.preprocessing.semantic_manifest.as_dict(),
                "activation_source_manifest": self.activation.as_dict(),
            },
            "model": {
                "outer_manifest": outer.as_dict(),
                "training_manifest": training.as_dict(),
                "checkpoints": checkpoint_rows,
            },
            "implementation": implementation_record,
            "model_rows": recent_rows,
            "station_order_model": contract["station_order_model"],
            "station_order_live": contract["station_order_live"],
            "expert_order": [
                "persistence",
                "seed0_p50",
                "seed1_p50",
                "seed2_p50",
                "seed3_p50",
                "seed4_p50",
            ],
        }
        input_payload["scientific_semantics_sha256"] = replay._canonical_sha256(
            replay._scientific_semantics(
                manifest=input_payload,
                issue_stations=station_rows,
                implementation=normalized_implementation,
            )
        )
        input_manifest = self._object(input_payload, ".json")
        issue_payload = {
            "schema_version": contract["issue_schema_version"],
            "target_date": target.isoformat(),
            "generated_at_utc": "2021-07-08T10:00:00Z",
            "source_as_of_at_utc": "2021-07-08T09:30:00Z",
            "source_snapshot_sha256": self.activation.sha256,
            "model_manifest_sha256": outer.sha256,
            "input_manifest": input_manifest.as_dict(),
            "stations": station_rows,
        }
        issue_raw = self._canonical(issue_payload)
        issue_path = self.paths.issue_inbox / f"{target.isoformat()}.json"
        issue_path.parent.mkdir(parents=True, exist_ok=True)
        issue_path.write_bytes(issue_raw)

        source = SimpleNamespace(
            frame=self.frame.copy(deep=True),
            watermark=date(2021, 7, 8),
            outcome_source_id="unit-test-survey-source",
            exported_at_utc=self.feed.exported_at_text,
            records=self.feed.records,
            dataset=self.preprocessing.canonical_dataset,
            semantic_manifest=self.preprocessing.semantic_manifest,
            activation_manifest=self.activation,
        )
        projection = SimpleNamespace(
            epoch_id="ready-epoch",
            last_finalized_date=date(2021, 7, 8),
            ledger_event_count=1,
            ledger_terminal_sequence_id=1,
            ledger_terminal_sha256="5" * 64,
        )
        prerequisites = SimpleNamespace(
            source=SimpleNamespace(
                sha256=self.activation.sha256,
                data_manifest=self.prerequisites.source.data_manifest,
            ),
            model=SimpleNamespace(sha256=outer.sha256),
        )
        return source, projection, prerequisites, issue_path

    def test_default_profile_and_independent_import_boundary_are_frozen(self) -> None:
        self.assertEqual(
            _sha256(replay.DEFAULT_CONFIG_PATH.read_bytes()),
            replay.DEFAULT_CONFIG_SHA256,
        )
        tree = ast.parse(Path(replay.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertFalse(
            any(
                name.startswith("convlstm")
                or name.endswith("ootang_issue_producer")
                for name in imported
            )
        )

    def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(self) -> None:
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}'):
            with self.subTest(raw=raw):
                with self.assertRaises(replay.IssueReplayIntegrityError):
                    replay._decode_json(raw, name="attack")

    def test_runtime_paths_reject_parent_escape_and_symlink_input(self) -> None:
        payload = json.loads(replay.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        payload["runtime"]["status"] = "../escape.json"
        altered = self.root / "altered.json"
        altered.write_text(json.dumps(payload), encoding="utf-8")
        profile = replay.load_replay_profile(altered)
        with self.assertRaises(replay.IssueReplayConfigError):
            replay.runtime_paths(profile, self.runtime)

        regular = self.root / "regular.json"
        regular.write_bytes(b"{}")
        link = self.root / "link.json"
        link.symlink_to(regular)
        with self.assertRaises(replay.IssueReplayIntegrityError):
            replay._read_regular_once(link, name="symlink", maximum_bytes=100)

    def test_runtime_paths_reject_preexisting_child_symlinks(self) -> None:
        attacks = (
            ("status", "issue_replay_status.json", False),
            ("receipts", "issue_replay_receipts", True),
            ("runner_lock", "runner.lock", False),
            ("objects_parent", "objects", True),
        )
        for name, relative, directory_target in attacks:
            with self.subTest(name=name):
                runtime = self.root / f"runtime-{name}"
                runtime.mkdir()
                target = self.root / f"outside-{name}"
                if directory_target:
                    target.mkdir()
                else:
                    target.write_bytes(b"outside")
                (runtime / relative).symlink_to(
                    target, target_is_directory=directory_target
                )
                with self.assertRaisesRegex(
                    replay.IssueReplayConfigError, "symbolic link"
                ):
                    replay.runtime_paths(self.profile, runtime)

    def test_read_once_fails_closed_across_pathname_swap(self) -> None:
        path = self.root / "captured.bin"
        replacement = self.root / "replacement.bin"
        original = b"captured-original-bytes"
        path.write_bytes(original)
        replacement.write_bytes(b"attacker-replacement")
        original_fdopen = os.fdopen

        class SwappingReader:
            def __init__(self, handle: object):
                self.handle = handle

            def __enter__(self) -> "SwappingReader":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int) -> bytes:
                os.replace(replacement, path)
                return self.handle.read(size)

        def swap(descriptor: int, *args: object, **kwargs: object) -> SwappingReader:
            return SwappingReader(original_fdopen(descriptor, *args, **kwargs))

        with mock.patch.object(replay.os, "fdopen", side_effect=swap):
            with self.assertRaisesRegex(
                replay.IssueReplayIntegrityError, "changed while being read"
            ):
                replay._read_regular_once(path, name="captured", maximum_bytes=100)
        self.assertEqual(path.read_bytes(), b"attacker-replacement")

    def test_read_once_rejects_swap_before_os_open_returns(self) -> None:
        path = self.root / "open-return-original.bin"
        replacement = self.root / "open-return-replacement.bin"
        path.write_bytes(b"opened old inode")
        replacement.write_bytes(b"pathname now names replacement")
        real_open = replay.os.open

        def open_then_swap(candidate: object, flags: int, *args: object) -> int:
            descriptor = real_open(candidate, flags, *args)
            os.replace(replacement, path)
            return descriptor

        with mock.patch.object(replay.os, "open", side_effect=open_then_swap):
            with self.assertRaisesRegex(
                replay.IssueReplayIntegrityError,
                "pathname changed before it was read",
            ):
                replay._read_regular_once(
                    path, name="open-return swap", maximum_bytes=100
                )

    def test_read_once_rejects_same_inode_mutation_during_read(self) -> None:
        path = self.root / "mutating.bin"
        path.write_bytes(b"original")
        original_fdopen = os.fdopen

        class MutatingReader:
            def __init__(self, handle: object):
                self.handle = handle

            def __enter__(self) -> "MutatingReader":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, size: int) -> bytes:
                value = self.handle.read(size)
                with path.open("ab") as target:
                    target.write(b"!")
                return value

        def mutate(descriptor: int, *args: object, **kwargs: object) -> MutatingReader:
            return MutatingReader(original_fdopen(descriptor, *args, **kwargs))

        with mock.patch.object(replay.os, "fdopen", side_effect=mutate):
            with self.assertRaisesRegex(
                replay.IssueReplayIntegrityError, "changed while being read"
            ):
                replay._read_regular_once(path, name="mutating", maximum_bytes=100)

    def test_real_safe_checkpoint_matches_producer_base_inference(self) -> None:
        artifact, _, independent = self._safe_checkpoint(0)
        deploy = producer_bundle.load_deploy_profile()
        source = producer_bundle.SourceBindings(
            training_cutoff_date=date.fromisoformat(
                str(self.training_source["training_cutoff_date"])
            ),
            activation_captured_at_utc=str(
                self.training_source["activation_captured_at_utc"]
            ),
            activation_source_manifest_sha256=str(
                self.training_source["activation_source_manifest_sha256"]
            ),
            dataset_sha256=str(self.training_source["dataset_sha256"]),
            semantic_manifest_sha256=str(
                self.training_source["semantic_manifest_sha256"]
            ),
        )
        producer = producer_bundle.load_safe_checkpoint(
            artifact.path,
            expected_sha256=artifact.sha256,
            expected_size_bytes=artifact.size_bytes,
            profile=deploy,
            source_bindings=source,
        )
        replay._configure_inference(self.profile)
        observed = replay._predict_checkpoint(
            independent, self._recent_rows(), self.profile
        )
        expected = producer_bundle._predict_checkpoint_p50_model_order(
            producer, self.frame, deploy
        )
        self.assertTrue(
            np.array_equal(
                np.asarray(
                    [observed[name] for name in self.profile["verification"]["station_order_model"]]
                ),
                expected,
            )
        )

    def test_full_ready_materials_publish_publicly_reloadable_receipt(self) -> None:
        source, projection, prerequisites, issue_path = self._ready_materials()
        verified = replay._verify_materials(
            profile=self.profile,
            paths=self.paths,
            source=source,
            projection=projection,
            prerequisites=prerequisites,
            issue_path=issue_path,
            now=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(verified.persistence_values_verified, 8)
        self.assertEqual(verified.p50_values_verified, 40)
        self.assertEqual(len(verified.comparisons), 40)
        self.assertLessEqual(verified.maximum_abs_difference_mm, 1e-6)
        payload = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 2, tzinfo=timezone.utc),
        )
        receipt_path = replay._receipt_path(self.paths, verified.target)
        self.assertTrue(
            replay._atomic_create(
                receipt_path, replay._canonical_bytes(payload, newline=True)
            )
        )
        loaded = replay.load_verified_replay_receipt(
            verified.target,
            runtime_root=self.runtime,
            expected_issue_sha256=verified.issue.sha256,
            expected_input_manifest_sha256=verified.input_manifest.sha256,
            expected_model_manifest_sha256=verified.model["outer_manifest"][
                "sha256"
            ],
            expected_ledger_pre_head=replay._ledger_pre_head(projection),
        )
        self.assertEqual(loaded.path, receipt_path)
        self.assertEqual(loaded.payload["verification"]["p50_values_verified"], 40)
        self.assertFalse(loaded.payload["verification"]["outcome_read"])

    def test_public_loader_rejects_self_consistent_daily_feed_tamper(self) -> None:
        source, projection, prerequisites, issue_path = self._ready_materials()
        verified = replay._verify_materials(
            profile=self.profile,
            paths=self.paths,
            source=source,
            projection=projection,
            prerequisites=prerequisites,
            issue_path=issue_path,
            now=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
        )
        receipt = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 2, tzinfo=timezone.utc),
        )

        semantic_path = Path(receipt["source"]["semantic_manifest"]["path"])
        semantic = replay._decode_json(
            semantic_path.read_bytes(), name="attack semantic manifest"
        )
        feed_reference = semantic["lineage"]["daily_feed_snapshot"]
        feed = replay._decode_json(
            Path(feed_reference["path"]).read_bytes(), name="attack daily feed"
        )
        rainfall_field = self.profile["_deploy_payload"]["source_feed"][
            "raw_fields"
        ]["rainfall"]
        feed["records"][-1][rainfall_field] += 1.0
        changed_feed = self._object(feed, ".feed.json")
        semantic["lineage"]["daily_feed_snapshot"].update(
            changed_feed.as_dict()
        )
        changed_semantic = self._object(semantic, ".source-manifest.json")

        input_payload = replay._decode_json(
            verified.input_manifest.path.read_bytes(), name="attack input manifest"
        )
        input_payload["source"]["semantic_manifest"] = changed_semantic.as_dict()
        issue_payload = replay._decode_json(
            issue_path.read_bytes(), name="attack issue"
        )
        implementation = replay._validate_issue_implementation(
            input_payload["implementation"], profile=self.profile
        )
        input_payload["scientific_semantics_sha256"] = replay._canonical_sha256(
            replay._scientific_semantics(
                manifest=input_payload,
                issue_stations=issue_payload["stations"],
                implementation=implementation,
            )
        )
        changed_input = self._object(input_payload, ".json")
        issue_payload["input_manifest"] = changed_input.as_dict()
        issue_raw = replay._canonical_bytes(issue_payload, newline=True)
        issue_path.write_bytes(issue_raw)
        changed_issue = replay.Artifact(
            issue_path.resolve(), _sha256(issue_raw), len(issue_raw)
        )

        receipt["source"]["semantic_manifest"] = changed_semantic.as_dict()
        receipt["input_manifest"] = changed_input.as_dict()
        receipt["issue"] = changed_issue.as_dict()
        receipt_path = replay._receipt_path(self.paths, verified.target)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(replay._canonical_bytes(receipt, newline=True))
        with self.assertRaisesRegex(
            replay.IssueReplayConflictError,
            "recursive shared-authority replay",
        ):
            replay.load_verified_replay_receipt(
                verified.target, runtime_root=self.runtime
            )

    def test_activation_feed_dataset_full_rebind_requires_recursive_authority(
        self,
    ) -> None:
        source, projection, prerequisites, issue_path = self._ready_materials()
        verified = replay._verify_materials(
            profile=self.profile,
            paths=self.paths,
            source=source,
            projection=projection,
            prerequisites=prerequisites,
            issue_path=issue_path,
            now=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
        )
        receipt = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 2, tzinfo=timezone.utc),
        )

        activation_payload = replay._decode_json(
            self.paths.activation_manifest.read_bytes(), name="attack activation"
        )
        semantic = replay._decode_json(
            Path(activation_payload["data_manifest"]["path"]).read_bytes(),
            name="attack activation semantic",
        )
        feed_reference = semantic["lineage"]["daily_feed_snapshot"]
        feed = replay._decode_json(
            Path(feed_reference["path"]).read_bytes(),
            name="attack activation feed",
        )
        feed["records"][-1]["revision_id"] += "-rebound"
        changed_feed = self._object(feed, ".feed.json")
        semantic["lineage"]["daily_feed_snapshot"].update(
            changed_feed.as_dict()
        )
        semantic["lineage"]["daily_feed_snapshot"][
            "date_revision_pairs_sha256"
        ] = _sha256(
            replay._canonical_bytes(
                [
                    [row["date"], row["revision_id"]]
                    for row in feed["records"]
                ],
                newline=True,
            )
        )

        dataset_payload = replay._decode_json(
            Path(semantic["canonical_dataset"]["path"]).read_bytes(),
            name="attack activation dataset",
        )
        noncanonical_dataset_raw = (
            json.dumps(
                dataset_payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        self.assertNotEqual(
            noncanonical_dataset_raw,
            replay._canonical_bytes(dataset_payload, newline=True),
        )
        changed_dataset = self._object(
            noncanonical_dataset_raw, ".source.json"
        )
        semantic["canonical_dataset"] = changed_dataset.as_dict()
        changed_semantic = self._object(semantic, ".source-manifest.json")
        activation_payload["data_manifest"] = changed_semantic.as_dict()
        activation_raw = replay._canonical_bytes(
            activation_payload, newline=True
        )
        self.paths.activation_manifest.write_bytes(activation_raw)
        changed_activation = replay.Artifact(
            self.paths.activation_manifest.resolve(),
            _sha256(activation_raw),
            len(activation_raw),
        )

        changed_training_source = {
            **self.training_source,
            "activation_source_manifest_sha256": changed_activation.sha256,
            "dataset_sha256": changed_dataset.sha256,
            "semantic_manifest_sha256": changed_semantic.sha256,
        }
        checkpoint_rows: list[dict[str, object]] = []
        for seed in self.profile["verification"]["seeds"]:
            checkpoint_payload = self._checkpoint_payload(seed)
            checkpoint_payload["source"] = changed_training_source
            buffer = io.BytesIO()
            torch.save(checkpoint_payload, buffer)
            checkpoint = self._object(buffer.getvalue(), "")
            checkpoint_rows.append(
                {"seed": seed, "artifact": checkpoint.as_dict()}
            )

        training = replay._decode_json(
            Path(verified.model["training_manifest"]["path"]).read_bytes(),
            name="attack rebound training",
        )
        deploy_model = self.profile["_deploy_payload"]["model"]
        identity = replay._canonical_sha256(
            {
                "schema_version": "ootang_five_seed_bundle_identity_v1",
                "source_bindings": changed_training_source,
                "input_schema_sha256": replay._input_schema_sha256(self.profile),
                "fit_policy": deploy_model["fit_policy"],
                "seeds": self.profile["verification"]["seeds"],
                "epochs": training["training_policy"]["epochs"],
                "training_windows": training["training_windows"],
                "checkpoints": [
                    {
                        "seed": row["seed"],
                        "sha256": row["artifact"]["sha256"],
                        "size_bytes": row["artifact"]["size_bytes"],
                    }
                    for row in checkpoint_rows
                ],
            }
        )
        model_version = f"{deploy_model['model_version_prefix']}-{identity[:16]}"
        training["model_version"] = model_version
        training["source_bindings"] = changed_training_source
        training["checkpoints"] = checkpoint_rows
        changed_training = self._object(training, "")

        outer = replay._decode_json(
            self.paths.model_manifest.read_bytes(), name="attack rebound outer"
        )
        outer["model_version"] = model_version
        outer["training_manifest"] = changed_training.as_dict()
        outer["checkpoints"] = checkpoint_rows
        outer_raw = replay._canonical_bytes(outer, newline=True)
        self.paths.model_manifest.write_bytes(outer_raw)
        changed_outer = replay.Artifact(
            self.paths.model_manifest.resolve(),
            _sha256(outer_raw),
            len(outer_raw),
        )

        input_payload = replay._decode_json(
            verified.input_manifest.path.read_bytes(), name="attack rebound input"
        )
        input_payload["source"][
            "activation_source_manifest"
        ] = changed_activation.as_dict()
        input_payload["model"] = {
            "outer_manifest": changed_outer.as_dict(),
            "training_manifest": changed_training.as_dict(),
            "checkpoints": checkpoint_rows,
        }
        issue = replay._decode_json(issue_path.read_bytes(), name="attack rebound issue")
        implementation = replay._validate_issue_implementation(
            input_payload["implementation"], profile=self.profile
        )
        input_payload["scientific_semantics_sha256"] = replay._canonical_sha256(
            replay._scientific_semantics(
                manifest=input_payload,
                issue_stations=issue["stations"],
                implementation=implementation,
            )
        )
        changed_input = self._object(input_payload, ".json")
        issue["source_snapshot_sha256"] = changed_activation.sha256
        issue["model_manifest_sha256"] = changed_outer.sha256
        issue["input_manifest"] = changed_input.as_dict()
        issue_raw = replay._canonical_bytes(issue, newline=True)
        issue_path.write_bytes(issue_raw)
        changed_issue = replay.Artifact(
            issue_path.resolve(), _sha256(issue_raw), len(issue_raw)
        )

        source.activation_manifest = changed_activation
        prerequisites.source.sha256 = changed_activation.sha256
        prerequisites.source.data_manifest = SimpleNamespace(
            path=changed_semantic.path,
            sha256=changed_semantic.sha256,
            size_bytes=changed_semantic.size_bytes,
        )
        prerequisites.model.sha256 = changed_outer.sha256
        receipt_path = replay._receipt_path(self.paths, verified.target)
        with self.assertRaisesRegex(
            replay.IssueReplayIntegrityError,
            "recursively verify activation/current causal source lineage",
        ):
            replay._verify_materials(
                profile=self.profile,
                paths=self.paths,
                source=source,
                projection=projection,
                prerequisites=prerequisites,
                issue_path=issue_path,
                now=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
            )
        self.assertFalse(receipt_path.exists())

        receipt["source"][
            "activation_source_manifest"
        ] = changed_activation.as_dict()
        receipt["source"][
            "activation_semantic_manifest"
        ] = changed_semantic.as_dict()
        receipt["source"][
            "activation_canonical_dataset"
        ] = changed_dataset.as_dict()
        receipt["model"] = {
            "outer_manifest": changed_outer.as_dict(),
            "training_manifest": changed_training.as_dict(),
            "checkpoints": checkpoint_rows,
            "model_version": model_version,
            "input_schema_sha256": replay._input_schema_sha256(self.profile),
        }
        receipt["input_manifest"] = changed_input.as_dict()
        receipt["issue"] = changed_issue.as_dict()
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(replay._canonical_bytes(receipt, newline=True))
        with self.assertRaisesRegex(
            replay.IssueReplayConflictError,
            "activation lineage failed recursive shared-authority replay",
        ):
            replay.load_verified_replay_receipt(
                verified.target, runtime_root=self.runtime
            )

    def test_declared_feed_export_after_issue_asof_never_leaves_receipt(self) -> None:
        source, projection, prerequisites, issue_path = self._ready_materials()
        verified = replay._verify_materials(
            profile=self.profile,
            paths=self.paths,
            source=source,
            projection=projection,
            prerequisites=prerequisites,
            issue_path=issue_path,
            now=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
        )
        payload = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 2, tzinfo=timezone.utc),
        )
        issue = replay._decode_json(issue_path.read_bytes(), name="late-feed issue")
        issue["source_as_of_at_utc"] = "2021-07-08T08:59:59Z"
        issue_raw = replay._canonical_bytes(issue, newline=True)
        issue_path.write_bytes(issue_raw)
        changed_issue = replay.Artifact(
            issue_path.resolve(), _sha256(issue_raw), len(issue_raw)
        )
        receipt_path = replay._receipt_path(self.paths, verified.target)
        with self.assertRaisesRegex(
            replay.IssueReplayIntegrityError,
            "declared current source was not available",
        ):
            replay._verify_materials(
                profile=self.profile,
                paths=self.paths,
                source=source,
                projection=projection,
                prerequisites=prerequisites,
                issue_path=issue_path,
                now=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
            )
        self.assertFalse(receipt_path.exists())

        payload["issue"] = changed_issue.as_dict()
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(replay._canonical_bytes(payload, newline=True))
        with self.assertRaisesRegex(
            replay.IssueReplayConflictError,
            "declared current source was not available",
        ):
            replay.load_verified_replay_receipt(
                verified.target, runtime_root=self.runtime
            )

    def test_declared_model_created_after_issue_never_leaves_receipt(self) -> None:
        source, projection, prerequisites, issue_path = self._ready_materials()
        verified = replay._verify_materials(
            profile=self.profile,
            paths=self.paths,
            source=source,
            projection=projection,
            prerequisites=prerequisites,
            issue_path=issue_path,
            now=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
        )
        payload = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 2, tzinfo=timezone.utc),
        )
        late_created = "2021-07-08T10:30:00Z"
        training_path = Path(verified.model["training_manifest"]["path"])
        training = replay._decode_json(
            training_path.read_bytes(), name="late-created training"
        )
        training["created_at_utc"] = late_created
        changed_training = self._object(training, "")

        outer = replay._decode_json(
            self.paths.model_manifest.read_bytes(), name="late-created outer"
        )
        outer["created_at_utc"] = late_created
        outer["training_manifest"] = changed_training.as_dict()
        outer_raw = replay._canonical_bytes(outer, newline=True)
        self.paths.model_manifest.write_bytes(outer_raw)
        changed_outer = replay.Artifact(
            self.paths.model_manifest.resolve(),
            _sha256(outer_raw),
            len(outer_raw),
        )

        input_payload = replay._decode_json(
            verified.input_manifest.path.read_bytes(), name="late-created input"
        )
        input_payload["model"]["outer_manifest"] = changed_outer.as_dict()
        input_payload["model"]["training_manifest"] = changed_training.as_dict()
        issue = replay._decode_json(issue_path.read_bytes(), name="late-created issue")
        implementation = replay._validate_issue_implementation(
            input_payload["implementation"], profile=self.profile
        )
        input_payload["scientific_semantics_sha256"] = replay._canonical_sha256(
            replay._scientific_semantics(
                manifest=input_payload,
                issue_stations=issue["stations"],
                implementation=implementation,
            )
        )
        changed_input = self._object(input_payload, ".json")
        issue["model_manifest_sha256"] = changed_outer.sha256
        issue["input_manifest"] = changed_input.as_dict()
        issue_raw = replay._canonical_bytes(issue, newline=True)
        issue_path.write_bytes(issue_raw)
        changed_issue = replay.Artifact(
            issue_path.resolve(), _sha256(issue_raw), len(issue_raw)
        )
        prerequisites.model.sha256 = changed_outer.sha256

        receipt_path = replay._receipt_path(self.paths, verified.target)
        with self.assertRaisesRegex(
            replay.IssueReplayIntegrityError,
            "declared activation/model/issue causal",
        ):
            replay._verify_materials(
                profile=self.profile,
                paths=self.paths,
                source=source,
                projection=projection,
                prerequisites=prerequisites,
                issue_path=issue_path,
                now=datetime(2021, 7, 8, 10, 31, tzinfo=timezone.utc),
            )
        self.assertFalse(receipt_path.exists())

        payload["model"]["outer_manifest"] = changed_outer.as_dict()
        payload["model"]["training_manifest"] = changed_training.as_dict()
        payload["input_manifest"] = changed_input.as_dict()
        payload["issue"] = changed_issue.as_dict()
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(replay._canonical_bytes(payload, newline=True))
        with self.assertRaisesRegex(
            replay.IssueReplayConflictError,
            "declared activation/model/issue causal",
        ):
            replay.load_verified_replay_receipt(
                verified.target, runtime_root=self.runtime
            )

    def test_public_loader_rejects_final_receipt_symlink(self) -> None:
        target = date(2021, 7, 9)
        receipt_path = replay._receipt_path(self.paths, target)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        backing = receipt_path.parent / "backing.json"
        backing.write_bytes(b"{}\n")
        receipt_path.symlink_to(backing)
        self.assertTrue(receipt_path.is_symlink())
        with self.assertRaisesRegex(
            replay.IssueReplayIntegrityError, "cannot open issue replay receipt"
        ):
            replay.load_verified_replay_receipt(
                target, runtime_root=self.runtime
            )

    def test_five_seed_replay_is_exactly_deterministic_for_40_p50_values(self) -> None:
        replay._configure_inference(self.profile)
        first: list[float] = []
        second: list[float] = []
        for seed in range(5):
            artifact, raw, loaded = self._safe_checkpoint(seed)
            repeated = replay._load_checkpoint(
                artifact,
                raw,
                expected_seed=seed,
                expected_source=self.training_source,
                expected_preprocessing=self.preprocessing,
                profile=self.profile,
            )
            for checkpoint, values in ((loaded, first), (repeated, second)):
                prediction = replay._predict_checkpoint(
                    checkpoint, self._recent_rows(), self.profile
                )
                values.extend(
                    prediction[station]
                    for station in self.profile["verification"]["station_order_live"]
                )
        self.assertEqual(len(first), 40)
        self.assertEqual(first, second)
        self.assertTrue(np.isfinite(first).all())

    def test_shape_valid_normalization_tamper_is_rejected(self) -> None:
        payload = copy.deepcopy(self._checkpoint_payload(0))
        payload["normalization"]["displacement_mean"][0] += 0.25
        with self.assertRaisesRegex(
            replay.IssueReplayIntegrityError, "immutable activation preprocessing"
        ):
            self._safe_checkpoint(0, payload)

    def test_checkpoint_unknown_key_and_nonfinite_tensor_are_rejected(self) -> None:
        unknown = self._checkpoint_payload(0)
        unknown["attacker"] = "extra"
        with self.assertRaises(replay.IssueReplayIntegrityError):
            self._safe_checkpoint(0, unknown)
        nonfinite = self._checkpoint_payload(0)
        nonfinite["state_dict"]["head.bias"][0] = float("nan")
        with self.assertRaises(replay.IssueReplayIntegrityError):
            self._safe_checkpoint(0, nonfinite)

    def test_checkpoint_reference_hash_tamper_fails_before_load(self) -> None:
        artifact, _, _ = self._safe_checkpoint(0)
        artifact.path.write_bytes(artifact.path.read_bytes() + b"tamper")
        with self.assertRaisesRegex(
            replay.IssueReplayIntegrityError, "hash or size mismatch"
        ):
            replay._artifact_from_reference(
                artifact.as_dict(),
                name="tampered checkpoint",
                expected_root=self.paths.objects,
            )

    def test_artifact_reference_rejects_symlink_alias_before_resolve(self) -> None:
        artifact = self._object(b'{"real":"object"}\n', ".json")
        alias = self.paths.objects / "alias.json"
        alias.symlink_to(artifact.path)
        reference = artifact.as_dict()
        reference["path"] = str(alias)
        with self.assertRaisesRegex(
            replay.IssueReplayIntegrityError, "symbolic link"
        ):
            replay._artifact_from_reference(
                reference, name="symlink alias", expected_root=self.paths.objects
            )

    def test_activation_normalization_matches_frozen_numpy_algorithm(self) -> None:
        displacement = self.frame[
            self.profile["verification"]["displacement_columns"]
        ].to_numpy(dtype=np.float64)
        exogenous = self.frame[
            self.profile["verification"]["exogenous_columns"]
        ].to_numpy(dtype=np.float64)
        lookback = self.profile["verification"]["lookback_days"]
        horizon = self.profile["verification"]["horizon_days"]
        windows = len(displacement) - lookback - horizon + 1
        delta = np.asarray(
            [
                displacement[index + lookback + horizon - 1]
                - displacement[index + lookback - 1]
                for index in range(windows)
            ],
            dtype=np.float64,
        )
        self.assertTrue(
            np.array_equal(self.preprocessing.displacement_mean, displacement.mean(0))
        )
        self.assertTrue(
            np.array_equal(
                self.preprocessing.displacement_scale,
                np.maximum(displacement.std(0), 1.0),
            )
        )
        self.assertTrue(
            np.array_equal(self.preprocessing.exogenous_mean, exogenous.mean(0))
        )
        self.assertTrue(
            np.array_equal(
                self.preprocessing.exogenous_scale,
                np.maximum(exogenous.std(0), 1e-6),
            )
        )
        self.assertTrue(
            np.array_equal(
                self.preprocessing.delta_scale,
                np.maximum(delta.std(0), 0.05),
            )
        )

    def test_all_five_normalization_arrays_match_production_preparation(self) -> None:
        deploy = producer_bundle.load_deploy_profile()
        dataset = self.preprocessing.canonical_dataset
        source = SimpleNamespace(
            frame=self.frame,
            watermark=date.fromisoformat(str(self.frame["Date"].iloc[-1])),
            dataset=producer_bundle.ArtifactBinding(
                dataset.path, dataset.sha256, dataset.size_bytes
            ),
        )
        prepared = producer_bundle.prepare_training_data(source, deploy)
        observed = self.preprocessing
        expected = prepared.normalization
        for name in (
            "displacement_mean",
            "displacement_scale",
            "exogenous_mean",
            "exogenous_scale",
            "delta_scale",
        ):
            with self.subTest(name=name):
                self.assertTrue(
                    np.array_equal(getattr(observed, name), getattr(expected, name))
                )

    def test_missing_runtime_is_machine_waiting_with_all_claims_false(self) -> None:
        empty_runtime = self.root / "empty-runtime"
        result = replay.verify_pending_issue(runtime_root=empty_runtime)
        self.assertEqual(result.status, "waiting_for_source_model_or_issue")
        status = replay._decode_json(
            result.status_path.read_bytes(), name="replay status"
        )
        self.assertEqual(status["schema_version"], "ootang_issue_replay_status_v1")
        self.assertFalse(status["outcome_read"])
        self.assertFalse(status["formal_warning_output"])
        self.assertFalse(status["e2_live_evidence_eligible"])
        self.assertFalse(status["real_activation_ready"])
        self.assertFalse(status["automatic_calibration_promotion"])

    def test_receipt_scratch_crash_residue_needs_no_human_cleanup(self) -> None:
        scratch = self.runtime / ".issue_replay_receipt_scratch"
        scratch.mkdir(parents=True)
        (scratch / ".2021-07-09.json.crash.tmp").write_bytes(
            b"partial machine write"
        )
        progress = replay.replay_progress_payload(runtime_root=self.runtime)
        self.assertEqual(progress["verified_receipt_count"], 0)

        self.paths.receipts.mkdir(parents=True, exist_ok=True)
        (self.paths.receipts / ".2021-07-09.json.crash.tmp").write_bytes(
            b"attacker approximation"
        )
        with self.assertRaisesRegex(
            replay.IssueReplayIntegrityError, "unknown hidden file"
        ):
            replay.replay_progress_payload(runtime_root=self.runtime)

    def test_runner_lock_contention_preserves_prior_status_bytes(self) -> None:
        result = replay.verify_pending_issue(runtime_root=self.runtime)
        first = result.status_path.read_bytes()
        lock = replay._acquire_lock(self.paths.runner_lock, name="test runner")
        try:
            with self.assertRaises(replay.IssueReplayBusyError):
                replay.verify_pending_issue(runtime_root=self.runtime)
        finally:
            replay._release_lock(lock)
        self.assertEqual(result.status_path.read_bytes(), first)

    def test_runner_handle_validation_rejects_static_symlink(self) -> None:
        outside = self.root / "outside-runner.lock"
        outside.touch()
        self.paths.runner_lock.parent.mkdir(parents=True, exist_ok=True)
        self.paths.runner_lock.symlink_to(outside)
        with outside.open("a+b") as handle:
            with self.assertRaises(replay.IssueReplayIntegrityError):
                replay._validate_runner_handle(handle, self.paths.runner_lock)

    def test_lock_acquire_rejects_pathname_inode_replacement(self) -> None:
        replacement = self.root / "replacement.lock"
        replacement.touch()
        original_flock = fcntl.flock

        def replace_after_flock(descriptor: int, operation: int) -> None:
            original_flock(descriptor, operation)
            os.replace(replacement, self.paths.runner_lock)

        with mock.patch.object(
            replay.fcntl, "flock", side_effect=replace_after_flock
        ):
            with self.assertRaisesRegex(
                replay.IssueReplayIntegrityError, "pathname changed"
            ):
                replay._acquire_lock(self.paths.runner_lock, name="raced runner")

    def test_completion_time_rejects_crossing_target_start(self) -> None:
        with self.assertRaisesRegex(
            replay.IssueReplayIntegrityError, "before target start"
        ):
            replay._verified_completion_time(
                started_at=datetime(2021, 7, 8, 15, 59, 59, tzinfo=timezone.utc),
                completed_at=datetime(2021, 7, 8, 16, 0, 0, tzinfo=timezone.utc),
                target=date(2021, 7, 9),
                timezone_name="Asia/Shanghai",
            )

    def test_completion_time_rejects_machine_clock_rollback(self) -> None:
        with self.assertRaisesRegex(replay.IssueReplayIntegrityError, "backward"):
            replay._verified_completion_time(
                started_at=datetime(2021, 7, 8, 10, 0, 1, tzinfo=timezone.utc),
                completed_at=datetime(2021, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
                target=date(2021, 7, 9),
                timezone_name="Asia/Shanghai",
            )

    def test_post_create_crossing_or_rollback_revokes_only_new_receipt(self) -> None:
        target = date(2021, 7, 9)
        raw = b'{"new":"receipt"}\n'
        completions = (
            datetime(2021, 7, 8, 16, 0, 0, tzinfo=timezone.utc),
            datetime(2021, 7, 8, 9, 59, 59, tzinfo=timezone.utc),
        )
        for index, completion in enumerate(completions):
            with self.subTest(index=index):
                path = self.paths.receipts / f"post-create-{index}.json"
                self.assertTrue(replay._atomic_create(path, raw))
                with self.assertRaises(replay.IssueReplayIntegrityError):
                    replay._post_create_time_fence(
                        receipt_path=path,
                        receipt_raw=raw,
                        verified_at=datetime(
                            2021, 7, 8, 10, 0, 0, tzinfo=timezone.utc
                        ),
                        target=target,
                        timezone_name="Asia/Shanghai",
                        clock=lambda completion=completion: completion,
                    )
                self.assertFalse(path.exists())

    def test_receipt_create_only_retry_preserves_first_bytes_and_progress(self) -> None:
        verified = self._verified_inputs()
        projection = SimpleNamespace(
            epoch_id="epoch-test",
            ledger_event_count=1,
            ledger_terminal_sequence_id=1,
            ledger_terminal_sha256="4" * 64,
        )
        first_payload = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 0, tzinfo=timezone.utc),
        )
        retry_payload = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 11, 0, tzinfo=timezone.utc),
        )
        path = replay._receipt_path(self.paths, verified.target)
        first = replay._canonical_bytes(first_payload, newline=True)
        self.assertTrue(replay._atomic_create(path, first))
        self.assertFalse(
            replay._atomic_create(
                path, replay._canonical_bytes(retry_payload, newline=True)
            )
        )
        self.assertEqual(path.read_bytes(), first)
        self.assertEqual(
            replay._receipt_semantics(first_payload),
            replay._receipt_semantics(retry_payload),
        )
        receipt = replay.load_verified_replay_receipt(
            verified.target,
            runtime_root=self.runtime,
            expected_issue_sha256=verified.issue.sha256,
            expected_input_manifest_sha256=verified.input_manifest.sha256,
            expected_model_manifest_sha256=verified.model["outer_manifest"][
                "sha256"
            ],
            expected_ledger_pre_head=replay._ledger_pre_head(projection),
        )
        self.assertEqual(receipt.path, path)
        first_progress = replay.replay_progress_payload(runtime_root=self.runtime)
        second_progress = replay.replay_progress_payload(runtime_root=self.runtime)
        self.assertEqual(first_progress, second_progress)
        self.assertEqual(first_progress["verified_receipt_count"], 1)
        self.assertFalse(first_progress["e2_live_evidence_eligible"])

    def test_receipt_tamper_and_semantic_conflict_fail_closed(self) -> None:
        verified = self._verified_inputs()
        projection = SimpleNamespace(
            epoch_id="epoch-test",
            ledger_event_count=1,
            ledger_terminal_sequence_id=1,
            ledger_terminal_sha256="4" * 64,
        )
        payload = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 0, tzinfo=timezone.utc),
        )
        changed = copy.deepcopy(payload)
        changed["verification"]["maximum_abs_difference_mm"] = 0.5e-6
        self.assertNotEqual(
            replay._receipt_semantics(payload), replay._receipt_semantics(changed)
        )
        payload["e2_live_evidence_eligible"] = True
        path = replay._receipt_path(self.paths, verified.target)
        replay._atomic_create(path, replay._canonical_bytes(payload, newline=True))
        with self.assertRaises(replay.IssueReplayConflictError):
            replay.load_verified_replay_receipt(
                verified.target, runtime_root=self.runtime
            )

    def test_receipt_nested_comparison_and_source_tamper_are_rejected(self) -> None:
        verified = self._verified_inputs()
        projection = SimpleNamespace(
            epoch_id="epoch-test",
            ledger_event_count=1,
            ledger_terminal_sequence_id=1,
            ledger_terminal_sha256="4" * 64,
        )
        original = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 0, tzinfo=timezone.utc),
        )
        attacks = []
        comparison = copy.deepcopy(original)
        comparison["verification"]["comparisons"][0][
            "replayed_p50_mm"
        ] += 0.25
        attacks.append(comparison)
        source = copy.deepcopy(original)
        source["source"]["canonical_dataset"]["sha256"] = "f" * 64
        attacks.append(source)
        path = replay._receipt_path(self.paths, verified.target)
        path.parent.mkdir(parents=True, exist_ok=True)
        for index, attack in enumerate(attacks):
            with self.subTest(index=index):
                path.write_bytes(replay._canonical_bytes(attack, newline=True))
                with self.assertRaises(replay.IssueReplayError):
                    replay.load_verified_replay_receipt(
                        verified.target, runtime_root=self.runtime
                    )

    def test_self_consistent_receipt_tamper_is_rejected_by_true_forward(self) -> None:
        source, projection, prerequisites, issue_path = self._ready_materials()
        issue = replay._decode_json(issue_path.read_bytes(), name="ready issue")
        issued = issue["stations"][0]
        issued["seed0_p50_mm"] += 0.5e-6
        old_input_path = Path(issue["input_manifest"]["path"])
        input_payload = replay._decode_json(
            old_input_path.read_bytes(), name="ready input"
        )
        implementation = replay._validate_issue_implementation(
            input_payload["implementation"], profile=self.profile
        )
        input_payload["scientific_semantics_sha256"] = replay._canonical_sha256(
            replay._scientific_semantics(
                manifest=input_payload,
                issue_stations=issue["stations"],
                implementation=implementation,
            )
        )
        rebound_input = self._object(input_payload, ".json")
        issue["input_manifest"] = rebound_input.as_dict()
        issue_path.write_bytes(replay._canonical_bytes(issue, newline=True))
        verified = replay._verify_materials(
            profile=self.profile,
            paths=self.paths,
            source=source,
            projection=projection,
            prerequisites=prerequisites,
            issue_path=issue_path,
            now=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
        )
        self.assertGreater(verified.maximum_abs_difference_mm, 0.0)
        self.assertLessEqual(verified.maximum_abs_difference_mm, 1e-6)
        payload = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 2, tzinfo=timezone.utc),
        )
        receipt_path = replay._receipt_path(self.paths, verified.target)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(replay._canonical_bytes(payload, newline=True))
        replay.load_verified_replay_receipt(
            verified.target, runtime_root=self.runtime
        )

        tampered = copy.deepcopy(payload)
        comparison = tampered["verification"]["comparisons"][0]
        comparison["replayed_p50_mm"] = comparison["issued_p50_mm"]
        comparison["absolute_difference_mm"] = 0.0
        comparisons = tampered["verification"]["comparisons"]
        tampered["verification"]["maximum_abs_difference_mm"] = max(
            row["absolute_difference_mm"] for row in comparisons
        )
        replay_projection: list[dict[str, object]] = []
        index = 0
        for station in self.profile["verification"]["station_order_live"]:
            row: dict[str, object] = {"station": station}
            for seed in self.profile["verification"]["seeds"]:
                row[f"seed{seed}_p50_mm"] = comparisons[index][
                    "replayed_p50_mm"
                ]
                index += 1
            replay_projection.append(row)
        tampered["verification"]["replayed_predictions_sha256"] = (
            replay._canonical_sha256(replay_projection)
        )
        receipt_path.write_bytes(
            replay._canonical_bytes(tampered, newline=True)
        )
        with self.assertRaisesRegex(
            replay.IssueReplayConflictError, "independent forward replay"
        ):
            replay.load_verified_replay_receipt(
                verified.target, runtime_root=self.runtime
            )

    def test_public_loader_rejects_source_tail_time_and_prehead_mismatch(self) -> None:
        verified = self._verified_inputs()
        projection = SimpleNamespace(
            epoch_id="epoch-test",
            ledger_event_count=1,
            ledger_terminal_sequence_id=1,
            ledger_terminal_sha256="4" * 64,
        )
        payload = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 2, tzinfo=timezone.utc),
        )
        path = replay._receipt_path(self.paths, verified.target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(replay._canonical_bytes(payload, newline=True))
        original_decoder = replay._decode_canonical_dataset_frame

        def altered_current(*args: object, **kwargs: object) -> pd.DataFrame:
            frame = original_decoder(*args, **kwargs)
            if kwargs.get("name") == "receipt current canonical dataset":
                frame = frame.copy(deep=True)
                column = self.profile["verification"]["displacement_columns"][0]
                frame.loc[frame.index[-1], column] += 1.0
            return frame

        with mock.patch.object(
            replay,
            "_decode_canonical_dataset_frame",
            side_effect=altered_current,
        ):
            with self.assertRaisesRegex(
                replay.IssueReplayConflictError,
                "source (?:tail|differs from recursive shared-authority replay)",
            ):
                replay.load_verified_replay_receipt(
                    verified.target, runtime_root=self.runtime
                )

        for mutate in ("time", "prehead"):
            with self.subTest(mutate=mutate):
                attack = copy.deepcopy(payload)
                if mutate == "time":
                    attack["verified_at_utc"] = "2021-07-08T09:59:59.000000Z"
                else:
                    attack["ledger_pre_head"]["sequence_id"] = 2
                path.write_bytes(replay._canonical_bytes(attack, newline=True))
                with self.assertRaises(replay.IssueReplayError):
                    replay.load_verified_replay_receipt(
                        verified.target, runtime_root=self.runtime
                    )

    def test_receipt_noncanonical_bytes_are_rejected(self) -> None:
        verified = self._verified_inputs()
        projection = SimpleNamespace(
            epoch_id="epoch-test",
            ledger_event_count=1,
            ledger_terminal_sequence_id=1,
            ledger_terminal_sha256="4" * 64,
        )
        payload = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 0, tzinfo=timezone.utc),
        )
        path = replay._receipt_path(self.paths, verified.target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        with self.assertRaisesRegex(
            replay.IssueReplayConflictError, "not canonical"
        ):
            replay.load_verified_replay_receipt(
                verified.target, runtime_root=self.runtime
            )

    def test_progress_identity_excludes_receipt_clock_but_binds_science(self) -> None:
        verified = self._verified_inputs()
        projection = SimpleNamespace(
            epoch_id="epoch-test",
            ledger_event_count=1,
            ledger_terminal_sequence_id=1,
            ledger_terminal_sha256="4" * 64,
        )
        first = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 0, tzinfo=timezone.utc),
        )
        second = replay._receipt_payload(
            verified,
            profile=self.profile,
            projection=projection,
            verified_at=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
        )
        first_receipt = replay.VerifiedReplayReceipt(
            self.paths.receipts / "first.json",
            replay._sha256_bytes(replay._canonical_bytes(first, newline=True)),
            len(replay._canonical_bytes(first, newline=True)),
            verified.target,
            first,
        )
        second_receipt = replay.VerifiedReplayReceipt(
            self.paths.receipts / "second.json",
            replay._sha256_bytes(replay._canonical_bytes(second, newline=True)),
            len(replay._canonical_bytes(second, newline=True)),
            verified.target,
            second,
        )
        self.assertNotEqual(first_receipt.sha256, second_receipt.sha256)
        self.assertEqual(
            replay._receipt_progress_record(first_receipt),
            replay._receipt_progress_record(second_receipt),
        )
        anchor_churn = copy.deepcopy(second)
        anchor_churn["ledger_pre_head"] = {
            **anchor_churn["ledger_pre_head"],
            "event_count": 9,
            "sequence_id": 9,
            "entry_sha256": "e" * 64,
        }
        anchor_churn_receipt = replay.VerifiedReplayReceipt(
            self.paths.receipts / "anchor-churn.json",
            "e" * 64,
            1,
            verified.target,
            anchor_churn,
        )
        self.assertEqual(
            replay._receipt_progress_record(first_receipt),
            replay._receipt_progress_record(anchor_churn_receipt),
        )
        storage_alias = copy.deepcopy(second)
        storage_alias["issue"]["path"] = "/another/runtime/issue.json"
        storage_alias["input_manifest"]["path"] = "/another/runtime/input.json"
        storage_alias["model"]["outer_manifest"]["path"] = (
            "/another/runtime/model.json"
        )
        storage_alias["implementation"]["verifier"]["path"] = (
            "/another/project/ootang_issue_replay.py"
        )
        storage_alias_receipt = replay.VerifiedReplayReceipt(
            self.paths.receipts / "storage-alias.json",
            "d" * 64,
            1,
            verified.target,
            storage_alias,
        )
        self.assertEqual(
            replay._receipt_progress_record(first_receipt),
            replay._receipt_progress_record(storage_alias_receipt),
        )
        changed = copy.deepcopy(second)
        changed["verification"]["replayed_predictions_sha256"] = "f" * 64
        changed_receipt = replay.VerifiedReplayReceipt(
            self.paths.receipts / "changed.json",
            "f" * 64,
            1,
            verified.target,
            changed,
        )
        self.assertNotEqual(
            replay._receipt_progress_record(first_receipt)[
                "receipt_semantics_sha256"
            ],
            replay._receipt_progress_record(changed_receipt)[
                "receipt_semantics_sha256"
            ],
        )


if __name__ == "__main__":
    unittest.main()
