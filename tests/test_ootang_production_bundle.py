"""Contracts for the E2-B content-addressed five-seed model bundle."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
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

from convlstm import model as base  # noqa: E402
from convlstm import ootang_production_bundle as bundle  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProductionBundleContracts(unittest.TestCase):
    """Build one cheap five-seed fixture and attack every persisted boundary."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="ootang-production-bundle-test-", dir=ROOT
        )
        cls.root = Path(cls._temporary.name)
        cls.runtime = cls.root / "runtime"
        cls.profile = bundle.load_deploy_profile()
        cls.source = cls._make_source()
        with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
            cls.loaded, cls.disposition = bundle.build_model_bundle(
                cls.source,
                cls.profile,
                runtime_root=cls.runtime,
                now=datetime(2021, 7, 8, 10, 0, tzinfo=timezone.utc),
                _epochs_override=1,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    @classmethod
    def _artifact(cls, name: str, payload: object) -> bundle.ArtifactBinding:
        path = cls.root / "source" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            raw = payload
        else:
            raw = (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        path.write_bytes(raw)
        return bundle.ArtifactBinding(path, _sha256(path), len(raw))

    @classmethod
    def _make_source(cls) -> SimpleNamespace:
        count = 373
        dates = pd.date_range("2020-07-01", periods=count, freq="D")
        time = np.arange(count, dtype=np.float64)
        frame = pd.DataFrame({"Date": dates.strftime("%Y-%m-%d")})
        for index, column in enumerate(cls.profile["model"]["displacement_columns"]):
            frame[column] = (
                100.0 * (index + 1)
                + 0.08 * (index + 1) * time
                + 0.4 * np.sin(time / (11.0 + index))
            )
        rain = 4.0 + 3.0 * (np.sin(time / 5.0) + 1.0)
        frame["RWL"] = 152.0 + 1.7 * np.sin(time / 23.0)
        frame["RWL_rate"] = np.diff(frame["RWL"], prepend=frame["RWL"].iloc[0])
        frame["Rain"] = rain
        for window in (7, 15, 30):
            frame[f"Rain_cum{window}"] = (
                pd.Series(rain).rolling(window, min_periods=1).sum().to_numpy()
            )
        watermark = dates[-1].date()
        dataset = cls._artifact(
            "dataset.json",
            {
                "schema_version": "ootang_canonical_model_source_v1",
                "case": "ootang",
                "date_timezone": cls.profile["source_feed"]["date_timezone"],
                "columns": list(frame.columns),
                "maximum_complete_finalized_date": watermark.isoformat(),
                "rows": [
                    [values[0], *[float(value) for value in values[1:]]]
                    for values in frame.itertuples(index=False, name=None)
                ],
            },
        )
        semantic = cls._artifact(
            "semantic.json",
            {
                "schema_version": "ootang_source_semantic_manifest_v1",
                "canonical_dataset": dataset.as_dict(),
            },
        )
        live_stations = cls.profile["source_feed"]["station_order_live"]
        model_station_to_column = dict(
            zip(
                cls.profile["source_feed"]["station_order_model"],
                cls.profile["model"]["displacement_columns"],
            )
        )
        latest = {
            station: float(frame[model_station_to_column[station]].iloc[-1])
            for station in live_stations
        }
        activation_payload = {
            "schema_version": "ootang_live_source_snapshot_v1",
            "case": "ootang",
            "captured_at_utc": "2021-07-08T08:00:00Z",
            "maximum_complete_finalized_date": watermark.isoformat(),
            "stations": live_stations,
            "outcome_source_id": "unit-test-survey-source",
            "data_manifest": semantic.as_dict(),
            "latest_finalized_displacement_mm": latest,
        }
        activation_path = (
            cls.runtime / cls.profile["runtime"]["activation_source_manifest"]
        )
        activation_path.parent.mkdir(parents=True, exist_ok=True)
        activation_raw = (
            json.dumps(
                activation_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        activation_path.write_bytes(activation_raw)
        activation = bundle.ArtifactBinding(
            activation_path.resolve(), _sha256(activation_path), len(activation_raw)
        )
        return SimpleNamespace(
            frame=frame,
            watermark=watermark,
            outcome_source_id="unit-test-survey-source",
            exported_at_utc="2021-07-08T08:00:00Z",
            dataset=dataset,
            semantic_manifest=semantic,
            activation_manifest=activation,
        )

    @classmethod
    def _source_for_runtime(
        cls, source: SimpleNamespace, runtime: Path
    ) -> SimpleNamespace:
        activation_path = runtime / cls.profile["runtime"][
            "activation_source_manifest"
        ]
        activation_path.parent.mkdir(parents=True, exist_ok=True)
        raw = source.activation_manifest.path.read_bytes()
        activation_path.write_bytes(raw)
        clone = SimpleNamespace(**vars(source))
        clone.activation_manifest = bundle.ArtifactBinding(
            activation_path.resolve(), hashlib.sha256(raw).hexdigest(), len(raw)
        )
        return clone

    def _write_checkpoint_payload(
        self, payload: dict[str, object], name: str
    ) -> bundle.ArtifactBinding:
        path = self.root / "attacks" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(payload, path)
        return bundle.ArtifactBinding(path, _sha256(path), path.stat().st_size)

    def test_profile_rejects_claiming_an_unverified_runtime_capability(self) -> None:
        payload = json.loads(bundle.DEFAULT_CONFIG_PATH.read_text())
        payload["engineering_capabilities"][
            "runner_independent_checkpoint_inference_replayed"
        ] = True
        path = self.root / "altered-deploy-profile.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(bundle.ProductionBundleConfigError):
            bundle.load_deploy_profile(path)

    def test_profile_runtime_paths_reject_absolute_and_parent_traversal(self) -> None:
        for attack in ("/tmp/ootang-escape", "../ootang-escape"):
            with self.subTest(attack=attack):
                payload = json.loads(bundle.DEFAULT_CONFIG_PATH.read_text())
                payload["runtime"]["model_manifest"] = attack
                path = self.root / f"runtime-path-{hashlib.sha256(attack.encode()).hexdigest()}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(
                    bundle.ProductionBundleConfigError, "relative path"
                ):
                    bundle.load_deploy_profile(path)

    def test_runtime_path_rejects_symlink_escape(self) -> None:
        runtime = self.root / "runtime-symlink-escape"
        outside = self.root / "outside-runtime"
        outside.mkdir()
        runtime.mkdir()
        (runtime / "model_bundle").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(
            bundle.ProductionBundleConfigError, "escapes"
        ):
            bundle._model_manifest_path(self.profile, runtime)

        runner_runtime = self.root / "runner-lock-symlink-escape"
        runner_runtime.mkdir()
        outside_lock = self.root / "outside-runner.lock"
        outside_lock.touch()
        (runner_runtime / "runner.lock").symlink_to(outside_lock)
        with self.assertRaisesRegex(
            bundle.ProductionBundleConfigError, "escapes"
        ):
            bundle._live_runner_lock_path(self.profile, runner_runtime)

    def test_builds_exact_outer_schema_and_five_seeds_without_best(self) -> None:
        self.assertEqual(self.disposition, "created")
        self.assertEqual(set(self.loaded.manifest), bundle.OUTER_MANIFEST_KEYS)
        self.assertEqual(self.loaded.manifest["seeds"], [0, 1, 2, 3, 4])
        self.assertIs(self.loaded.manifest["best_seed_selected"], False)
        self.assertEqual(
            [checkpoint.seed for checkpoint in self.loaded.checkpoints],
            [0, 1, 2, 3, 4],
        )

    def test_checkpoints_are_plain_weights_only_tensor_bundles(self) -> None:
        allowed = (str, int, float, bool, type(None), torch.Tensor)

        def assert_safe(value: object) -> None:
            if isinstance(value, dict):
                self.assertTrue(all(isinstance(key, str) for key in value))
                for child in value.values():
                    assert_safe(child)
            elif isinstance(value, list):
                for child in value:
                    assert_safe(child)
            else:
                self.assertIsInstance(value, allowed)

        for checkpoint in self.loaded.checkpoints:
            payload = torch.load(
                checkpoint.artifact.path, map_location="cpu", weights_only=True
            )
            self.assertEqual(set(payload), bundle.CHECKPOINT_KEYS)
            assert_safe(payload)

    def test_station_order_and_preprocessing_metadata_are_persisted(self) -> None:
        checkpoint = self.loaded.checkpoints[0]
        payload = torch.load(
            checkpoint.artifact.path, map_location="cpu", weights_only=True
        )
        self.assertEqual(
            payload["feature_schema"]["stations_model"],
            self.profile["source_feed"]["station_order_model"],
        )
        self.assertEqual(
            list(self.loaded.stations_live),
            self.profile["source_feed"]["station_order_live"],
        )
        self.assertEqual(
            tuple(payload["normalization"]["displacement_mean"].shape), (8,)
        )
        self.assertEqual(
            tuple(payload["spatial"]["readout_weights"].shape),
            (8, base.GRID_H * base.GRID_W),
        )

    def test_persistable_preprocessing_equals_base_implementation(self) -> None:
        prepared = bundle.prepare_training_data(self.source, self.profile)
        stations, xy, _ = base.load_station_geometry(base.DISP_COLS)
        self.assertEqual(stations, self.profile["source_feed"]["station_order_model"])
        interpolate, _ = base.make_interpolator(xy, base.GRID_H, base.GRID_W)
        expected, _ = base.make_model_inputs(
            prepared.frame,
            prepared.displacement,
            len(prepared.frame),
            interpolate,
            elevation_grid=prepared.elevation_grid,
        )
        self.assertTrue(np.array_equal(prepared.inputs, expected))

    def test_reloaded_prediction_matches_and_returns_live_order(self) -> None:
        predictions = bundle.predict_p50(
            self.loaded, self.source.frame, profile=self.profile
        )
        self.assertEqual(list(predictions), [0, 1, 2, 3, 4])
        for values in predictions.values():
            self.assertEqual(
                list(values), self.profile["source_feed"]["station_order_live"]
            )
            self.assertTrue(np.isfinite(list(values.values())).all())
        training = json.loads(self.loaded.training_manifest.path.read_text())
        for replay in training["reload_replay"]:
            self.assertTrue(replay["equivalent"])
            self.assertLessEqual(
                replay["max_abs_difference_mm"],
                replay["absolute_tolerance_mm"],
            )

    def test_existing_identical_target_is_idempotent(self) -> None:
        with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
            reused, disposition = bundle.build_model_bundle(
                self.source,
                self.profile,
                runtime_root=self.runtime,
                now=datetime(2021, 7, 10, 3, 0, tzinfo=timezone.utc),
                _epochs_override=1,
            )
        self.assertEqual(disposition, "reused_identical")
        self.assertEqual(reused.manifest_sha256, self.loaded.manifest_sha256)
        self.assertEqual(reused.created_at_utc, self.loaded.created_at_utc)

    def test_independent_rebuild_is_deterministic_for_all_seeds(self) -> None:
        second_runtime = self.root / "deterministic-rebuild"
        second_source = self._source_for_runtime(self.source, second_runtime)
        with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
            second, disposition = bundle.build_model_bundle(
                second_source,
                self.profile,
                runtime_root=second_runtime,
                now=datetime(2021, 7, 8, 10, 0, tzinfo=timezone.utc),
                _epochs_override=1,
            )
        self.assertEqual(disposition, "created")
        self.assertEqual(second.model_version, self.loaded.model_version)
        self.assertEqual(
            [checkpoint.artifact.sha256 for checkpoint in second.checkpoints],
            [checkpoint.artifact.sha256 for checkpoint in self.loaded.checkpoints],
        )
        self.assertEqual(
            bundle.predict_p50(second, self.source.frame, profile=self.profile),
            bundle.predict_p50(self.loaded, self.source.frame, profile=self.profile),
        )

    def test_default_loader_rejects_reduced_epoch_test_artifact(self) -> None:
        with self.assertRaisesRegex(
            bundle.ProductionBundleIntegrityError, "Reduced-epoch checkpoint"
        ):
            bundle.load_deploy_bundle(
                self.profile,
                runtime_root=self.runtime,
                source=self.source,
                now=datetime(2021, 7, 10, 3, 0, tzinfo=timezone.utc),
            )
        with self.assertRaisesRegex(
            bundle.ProductionBundleConfigError, "explicit test mode"
        ):
            bundle.load_deploy_bundle(
                self.profile,
                runtime_root=self.runtime,
                source=self.source,
                now=datetime(2021, 7, 10, 3, 0, tzinfo=timezone.utc),
                _allow_test_epochs=True,
            )
        with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
            explicitly_test_loaded = bundle.load_deploy_bundle(
                self.profile,
                runtime_root=self.runtime,
                source=self.source,
                now=datetime(2021, 7, 10, 3, 0, tzinfo=timezone.utc),
                _allow_test_epochs=True,
            )
        self.assertEqual(explicitly_test_loaded.model_version, self.loaded.model_version)

    def test_creation_time_must_follow_capture_and_precede_target(self) -> None:
        captured = datetime(2021, 7, 8, 8, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(
            bundle.ProductionBundleIntegrityError, "predates"
        ):
            bundle._validate_bundle_time_boundary(
                self.loaded.source_bindings,
                self.profile,
                created_at=captured - pd.Timedelta(seconds=1),
                machine_now=captured,
            )
        with self.assertRaisesRegex(
            bundle.ProductionBundleIntegrityError, "target natural day"
        ):
            bundle._validate_bundle_time_boundary(
                self.loaded.source_bindings,
                self.profile,
                created_at=datetime(2021, 7, 8, 16, 0, tzinfo=timezone.utc),
                machine_now=datetime(2021, 7, 8, 16, 0, tzinfo=timezone.utc),
            )

    def test_publication_crossing_target_start_is_revoked_fail_closed(self) -> None:
        runtime = self.root / "crossed-target"
        manifest_path = runtime / self.profile["runtime"]["model_manifest"]
        manifest_raw = b'{"sentinel":"not-activated"}\n'
        runner_lock = bundle._live_runner_lock_path(self.profile, runtime)
        observed_while_published: list[bool] = []
        atomic_write = bundle._atomic_write_bytes

        def publish_and_probe(
            path: Path, value: bytes, *, replace: bool
        ) -> bool:
            created = atomic_write(path, value, replace=replace)
            observed_while_published.append(path.is_file())
            with runner_lock.open("a+b") as competing_runner:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(
                        competing_runner.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
            return created

        with mock.patch.object(
            bundle,
            "_utc_now",
            side_effect=[
                datetime(2021, 7, 8, 15, 59, 59, tzinfo=timezone.utc),
                datetime(2021, 7, 8, 16, 0, 0, tzinfo=timezone.utc),
            ],
        ), mock.patch.object(
            bundle, "_atomic_write_bytes", side_effect=publish_and_probe
        ):
            with self.assertRaisesRegex(
                bundle.ProductionBundleIntegrityError, "publication reached"
            ):
                bundle._publish_bundle_manifest(
                    manifest_path,
                    manifest_raw,
                    bindings=self.loaded.source_bindings,
                    profile=self.profile,
                    created_at=datetime(
                        2021, 7, 8, 15, 59, 58, tzinfo=timezone.utc
                    ),
                    now=None,
                    runtime_root=runtime,
                )
        self.assertEqual(observed_while_published, [True])
        self.assertFalse(manifest_path.exists())

    def test_runner_lock_contention_prevents_outer_manifest_publication(self) -> None:
        runtime = self.root / "runner-busy-publication"
        manifest_path = runtime / self.profile["runtime"]["model_manifest"]
        runner_lock = bundle._live_runner_lock_path(self.profile, runtime)
        runner_lock.parent.mkdir(parents=True, exist_ok=True)
        with runner_lock.open("a+b") as competing_runner:
            fcntl.flock(
                competing_runner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
            )
            with self.assertRaisesRegex(
                bundle.ProductionBundleBusyError, "runner owns"
            ):
                bundle._publish_bundle_manifest(
                    manifest_path,
                    b'{"sentinel":"not-published"}\n',
                    bindings=self.loaded.source_bindings,
                    profile=self.profile,
                    created_at=datetime(
                        2021, 7, 8, 15, 59, 58, tzinfo=timezone.utc
                    ),
                    now=datetime(
                        2021, 7, 8, 15, 59, 59, tzinfo=timezone.utc
                    ),
                    runtime_root=runtime,
                )
        self.assertFalse(manifest_path.exists())

    def test_epoch_override_is_unavailable_outside_explicit_test_mode(self) -> None:
        runtime = self.root / "override-rejected"
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(bundle.ProductionBundleConfigError):
                bundle.build_model_bundle(
                    self.source,
                    self.profile,
                    runtime_root=runtime,
                    _epochs_override=1,
                )
        self.assertFalse((runtime / "model_bundle" / "manifest.json").exists())

    def test_checkpoint_hash_tamper_is_rejected_before_load(self) -> None:
        checkpoint = self.loaded.checkpoints[0]
        tampered = self.root / "attacks" / "tampered-checkpoint.pt"
        tampered.parent.mkdir(parents=True, exist_ok=True)
        tampered.write_bytes(checkpoint.artifact.path.read_bytes() + b"tamper")
        with self.assertRaisesRegex(
            bundle.ProductionBundleIntegrityError, "SHA-256 mismatch"
        ):
            bundle.load_safe_checkpoint(
                tampered,
                expected_sha256=checkpoint.artifact.sha256,
                profile=self.profile,
                source_bindings=self.loaded.source_bindings,
            )

    def test_checkpoint_hash_and_load_use_the_same_captured_bytes(self) -> None:
        checkpoint = self.loaded.checkpoints[0]
        attack = self.root / "attacks" / "checkpoint-replaced-after-read.pt"
        original = checkpoint.artifact.path.read_bytes()
        attack.parent.mkdir(parents=True, exist_ok=True)
        attack.write_bytes(original)
        artifact = bundle.ArtifactBinding(
            attack.resolve(), hashlib.sha256(original).hexdigest(), len(original)
        )
        real_torch_load = torch.load

        def replace_path_after_capture(
            captured: object, *args: object, **kwargs: object
        ) -> object:
            self.assertIsInstance(captured, io.BytesIO)
            attack.write_bytes(b"replacement-after-capture")
            return real_torch_load(captured, *args, **kwargs)

        with mock.patch.object(
            bundle.torch, "load", side_effect=replace_path_after_capture
        ):
            loaded = bundle.load_safe_checkpoint(
                artifact,
                profile=self.profile,
                source_bindings=self.loaded.source_bindings,
            )
        self.assertEqual(loaded.seed, 0)
        self.assertEqual(attack.read_bytes(), b"replacement-after-capture")

    def test_unknown_schema_key_is_rejected_after_safe_load(self) -> None:
        checkpoint = self.loaded.checkpoints[0]
        payload = torch.load(
            checkpoint.artifact.path, map_location="cpu", weights_only=True
        )
        payload["unknown"] = False
        attack = self._write_checkpoint_payload(payload, "unknown-key.pt")
        with self.assertRaisesRegex(
            bundle.ProductionBundleIntegrityError, "keys changed"
        ):
            bundle.load_safe_checkpoint(
                attack, profile=self.profile, source_bindings=self.loaded.source_bindings
            )

    def test_mismatched_tensor_shape_is_rejected(self) -> None:
        checkpoint = self.loaded.checkpoints[0]
        payload = torch.load(
            checkpoint.artifact.path, map_location="cpu", weights_only=True
        )
        payload["normalization"]["delta_scale"] = torch.ones(
            7, dtype=torch.float64
        )
        attack = self._write_checkpoint_payload(payload, "wrong-shape.pt")
        with self.assertRaisesRegex(
            bundle.ProductionBundleIntegrityError, "tensor metadata changed"
        ):
            bundle.load_safe_checkpoint(
                attack, profile=self.profile, source_bindings=self.loaded.source_bindings
            )

    def test_source_binding_mismatch_is_rejected(self) -> None:
        mismatch = replace(self.loaded.source_bindings, dataset_sha256="f" * 64)
        with self.assertRaisesRegex(
            bundle.ProductionBundleIntegrityError, "current source"
        ):
            bundle.load_safe_checkpoint(
                self.loaded.checkpoints[0].artifact,
                profile=self.profile,
                source_bindings=mismatch,
            )

    def test_advanced_current_source_cannot_replace_activation_epoch(self) -> None:
        advanced = SimpleNamespace(**vars(self.source))
        advanced.watermark = self.source.watermark + pd.Timedelta(days=1)
        with self.assertRaisesRegex(
            bundle.ProductionBundleIntegrityError, "immutable activation epoch"
        ):
            bundle.build_model_bundle(
                advanced,
                self.profile,
                runtime_root=self.root / "advanced-current",
            )

    def test_injected_activation_reference_outside_runtime_is_rejected(self) -> None:
        runtime = self.root / "activation-injection-runtime"
        configured = self._source_for_runtime(self.source, runtime)
        forged_path = self.root / "forged-activation.json"
        forged_raw = configured.activation_manifest.path.read_bytes()
        forged_path.write_bytes(forged_raw)
        forged = SimpleNamespace(**vars(configured))
        forged.activation_manifest = bundle.ArtifactBinding(
            forged_path.resolve(), hashlib.sha256(forged_raw).hexdigest(), len(forged_raw)
        )
        with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
            with self.assertRaisesRegex(
                bundle.ProductionBundleIntegrityError, "configured immutable path"
            ):
                bundle.build_model_bundle(
                    forged,
                    self.profile,
                    runtime_root=runtime,
                    _epochs_override=1,
                )

    def test_changed_same_target_conflicts_without_overwrite(self) -> None:
        conflict_runtime = self.root / "conflict-runtime"
        conflict_source = self._source_for_runtime(self.source, conflict_runtime)
        conflict_manifest = conflict_runtime / "model_bundle" / "manifest.json"
        conflict_manifest.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(self.loaded.manifest_path.read_text())
        payload["model_version"] += "-changed"
        conflict_manifest.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        before = conflict_manifest.read_bytes()
        with self.assertRaises(bundle.ProductionBundleConflictError):
            bundle.build_model_bundle(
                conflict_source, self.profile, runtime_root=conflict_runtime
            )
        self.assertEqual(conflict_manifest.read_bytes(), before)

    def test_changed_epoch_semantics_conflict_with_existing_target(self) -> None:
        before = self.loaded.manifest_path.read_bytes()
        with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
            with self.assertRaisesRegex(
                bundle.ProductionBundleConflictError, "different epoch semantics"
            ):
                bundle.build_model_bundle(
                    self.source,
                    self.profile,
                    runtime_root=self.runtime,
                    now=datetime(2021, 7, 10, 3, 0, tzinfo=timezone.utc),
                    _epochs_override=2,
                )
        self.assertEqual(self.loaded.manifest_path.read_bytes(), before)

    def test_machine_poll_waits_without_source_or_training(self) -> None:
        waiting_runtime = self.root / "waiting-runtime"
        status_path = bundle.run_once(
            runtime_root=waiting_runtime,
            now=datetime(2021, 7, 10, 4, 0, tzinfo=timezone.utc),
        )
        status = json.loads(status_path.read_text())
        self.assertEqual(
            status["bundle_status"], "waiting_for_semantically_validated_source"
        )
        self.assertFalse((waiting_runtime / "objects" / "sha256").exists())

    def test_lock_contention_is_busy_exit_three_without_status_overwrite(self) -> None:
        busy_runtime = self.root / "busy-runtime"
        status_path = busy_runtime / self.profile["runtime"]["bundle_status"]
        status_path.parent.mkdir(parents=True, exist_ok=True)
        preserved = b'{"bundle_status":"ready","sentinel":"preserve"}\n'
        status_path.write_bytes(preserved)
        lock_path = busy_runtime / self.profile["runtime"]["deploy_lock"]
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(
                bundle.ProductionBundleBusyError, "owns the lock"
            ):
                bundle.run_once(runtime_root=busy_runtime)
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                exit_code = bundle.main(["--runtime-root", str(busy_runtime)])
            self.assertEqual(exit_code, 3)
            self.assertIn("busy:", stderr.getvalue())
            self.assertEqual(status_path.read_bytes(), preserved)
            self.assertFalse((busy_runtime / "objects" / "sha256").exists())

    def test_status_distinguishes_safe_reload_from_inference_replay(self) -> None:
        status_path = bundle._write_status(
            self.profile,
            runtime_root=self.runtime,
            now=datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc),
            bundle_status="ready",
            reason="reused_identical",
            bundle=self.loaded,
            checkpoint_inference_replayed=False,
        )
        status = json.loads(status_path.read_text())
        self.assertIs(status["safe_checkpoint_loading_exercised"], True)
        self.assertIs(status["producer_checkpoint_inference_replayed"], False)

        status_path = bundle._write_status(
            self.profile,
            runtime_root=self.runtime,
            now=datetime(2021, 7, 8, 10, 2, tzinfo=timezone.utc),
            bundle_status="ready",
            reason="created",
            bundle=self.loaded,
            checkpoint_inference_replayed=True,
        )
        status = json.loads(status_path.read_text())
        self.assertIs(status["producer_checkpoint_inference_replayed"], True)


if __name__ == "__main__":
    unittest.main()
