"""Contracts for the future-only E2-B issue producer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from convlstm import ootang_production_bundle as bundle  # noqa: E402
from monitoring import ootang_issue_producer as producer  # noqa: E402
from monitoring import ootang_live_source as source  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class _Artifact:
    path: Path
    sha256: str
    size_bytes: int


class IssueProducerUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = producer.load_config()
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-issue-producer-test-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _artifact(self, name: str, content: bytes = b"artifact\n") -> _Artifact:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return _Artifact(path.resolve(), _sha256(path), path.stat().st_size)

    def test_profile_rejects_claiming_an_unverified_runtime_capability(self) -> None:
        payload = json.loads(producer.DEFAULT_CONFIG_PATH.read_text())
        payload["engineering_capabilities"]["e2_live_evidence_eligible"] = True
        path = self.root / "altered-deploy-profile.json"
        _write_json(path, payload)

        with self.assertRaises(producer.IssueProducerConfigError):
            producer.load_config(path)

    def test_profile_rejects_extra_scientific_contract_keys(self) -> None:
        for section in ("source_feed", "model", "issue"):
            with self.subTest(section=section):
                payload = json.loads(producer.DEFAULT_CONFIG_PATH.read_text())
                payload[section]["unexpected_contract_key"] = True
                path = self.root / f"altered-{section}.json"
                _write_json(path, payload)
                with self.assertRaisesRegex(
                    producer.IssueProducerConfigError, f"{section} keys changed"
                ):
                    producer.load_config(path)

    def test_static_artifact_hash_failure_precedes_missing_runtime_wait(self) -> None:
        artifacts = {
            "historical_base": ROOT / "data" / "monitoring_data.csv",
            "station_geometry": ROOT / "data" / "station_coords.csv",
        }
        for corrupted in artifacts:
            with self.subTest(corrupted=corrupted):
                project = self.root / f"project-{corrupted}"
                config_dir = project / "config"
                data_dir = project / "data"
                config_dir.mkdir(parents=True)
                data_dir.mkdir(parents=True)
                (config_dir / "ootang_prequential_live.v1.json").write_bytes(
                    (ROOT / "config" / "ootang_prequential_live.v1.json").read_bytes()
                )
                for name, original in artifacts.items():
                    destination = data_dir / original.name
                    destination.write_bytes(
                        b"tampered\n" if name == corrupted else original.read_bytes()
                    )
                deploy_path = config_dir / "ootang_prequential_deploy.v1.json"
                deploy_path.write_bytes(producer.DEFAULT_CONFIG_PATH.read_bytes())
                runtime = project / "empty-runtime"
                with self.assertRaisesRegex(
                    producer.IssueProducerConfigError,
                    f"Bound {corrupted} SHA-256 changed",
                ):
                    producer.produce_issue(
                        config_path=deploy_path,
                        runtime_root=runtime,
                        project_root=project,
                        clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
                    )
                self.assertFalse(
                    (runtime / self.profile["runtime"]["issue_status"]).exists()
                )

    def test_profile_rejects_an_unreviewed_runtime_path(self) -> None:
        payload = json.loads(producer.DEFAULT_CONFIG_PATH.read_text())
        payload["runtime"]["manual_override"] = "manual.json"
        path = self.root / "altered-runtime-profile.json"
        _write_json(path, payload)

        with self.assertRaises(producer.IssueProducerConfigError):
            producer.load_config(path)

    def _input_manifest(
        self,
        name: str,
        scientific: str = "a" * 64,
        marker: str = "first",
        dataset_content: bytes = b"same dataset\n",
    ) -> producer.Artifact:
        prefix = Path(name).stem
        dataset = self._artifact(f"{prefix}/dataset.json", dataset_content)
        semantic = self._artifact(
            f"{prefix}/semantic.json", f"capture={marker}\n".encode()
        )
        activation = self._artifact(f"{prefix}/activation.json", b"activation\n")
        outer = self._artifact(f"{prefix}/outer.json", b"same outer\n")
        training = self._artifact(f"{prefix}/training.json", b"same training\n")
        checkpoints = [
            self._artifact(f"{prefix}/checkpoint-{seed}.pt", f"seed={seed}\n".encode())
            for seed in range(5)
        ]
        model_order = self.profile["source_feed"]["station_order_model"]
        live_order = self.profile["source_feed"]["station_order_live"]
        expert_order = self.profile["issue"]["expert_order"]
        model_rows = []
        for offset, day in enumerate(pd.date_range("2029-12-26", periods=7)):
            model_rows.append(
                {
                    "date": day.date().isoformat(),
                    **{f"{station}_disp": float(offset) for station in model_order},
                    "RWL": 150.0,
                    "RWL_rate": 0.1,
                    "Rain_cum7": 7.0,
                    "Rain_cum15": 15.0,
                    "Rain_cum30": 30.0,
                }
            )
        stations = self._station_payload()
        implementation = producer._implementation_record(self.profile)
        stable = producer._scientific_semantics(
            target_date="2030-01-02",
            source_watermark="2030-01-01",
            dataset_sha256=dataset.sha256,
            activation_sha256=activation.sha256,
            model_manifest_sha256=outer.sha256,
            training_manifest_sha256=training.sha256,
            checkpoints=[
                (seed, artifact.sha256) for seed, artifact in enumerate(checkpoints)
            ],
            model_rows=model_rows,
            issued_stations=stations,
            station_order_model=model_order,
            station_order_live=live_order,
            expert_order=expert_order,
            implementation=producer._validate_implementation_record(implementation),
        )
        path = self.root / name
        _write_json(
            path,
            {
                "schema_version": "ootang_issue_input_manifest_v1",
                "case": "ootang",
                "target_date": "2030-01-02",
                "source_watermark": "2030-01-01",
                "scientific_semantics_sha256": (
                    producer._canonical_digest(stable)
                    if scientific == "a" * 64
                    else scientific
                ),
                "source": {
                    "canonical_dataset": producer._artifact_payload(dataset),
                    "semantic_manifest": producer._artifact_payload(semantic),
                    "activation_source_manifest": producer._artifact_payload(
                        activation
                    ),
                },
                "model": {
                    "outer_manifest": producer._artifact_payload(outer),
                    "training_manifest": producer._artifact_payload(training),
                    "checkpoints": [
                        {
                            "seed": seed,
                            "artifact": producer._artifact_payload(artifact),
                        }
                        for seed, artifact in enumerate(checkpoints)
                    ],
                },
                "implementation": implementation,
                "model_rows": model_rows,
                "station_order_model": model_order,
                "station_order_live": live_order,
                "expert_order": expert_order,
            },
        )
        return producer.Artifact(path.resolve(), _sha256(path), path.stat().st_size)

    def _station_payload(self) -> list[dict[str, object]]:
        return [
            {
                "station": station,
                "persistence_mm": 1.0,
                "seed0_p50_mm": 2.0,
                "seed1_p50_mm": 3.0,
                "seed2_p50_mm": 4.0,
                "seed3_p50_mm": 5.0,
                "seed4_p50_mm": 6.0,
            }
            for station in self.profile["source_feed"]["station_order_live"]
        ]

    def _issue_payload(
        self,
        manifest: producer.Artifact,
        *,
        generated: str = "2030-01-01T14:00:00Z",
        source_as_of: str = "2030-01-01T13:00:00Z",
    ) -> dict[str, object]:
        manifest_payload = json.loads(manifest.path.read_text(encoding="utf-8"))
        return {
            "schema_version": "ootang_live_issue_batch_v1",
            "target_date": "2030-01-02",
            "generated_at_utc": generated,
            "source_as_of_at_utc": source_as_of,
            "source_snapshot_sha256": manifest_payload["source"][
                "activation_source_manifest"
            ]["sha256"],
            "model_manifest_sha256": manifest_payload["model"]["outer_manifest"][
                "sha256"
            ],
            "input_manifest": producer._artifact_payload(manifest),
            "stations": self._station_payload(),
        }

    def _publish(self, path: Path, payload: dict[str, object]) -> bool:
        return producer._publish_issue_idempotently(
            path,
            payload,
            receipt_root=self.root / "issue_receipts",
            object_root=self.root / "objects" / "sha256",
        )

    def test_missing_source_and_model_is_machine_waiting(self) -> None:
        result = producer.produce_issue(
            runtime_root=self.root,
            clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result.status, "waiting_for_source_or_model")
        status = json.loads(result.status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["producer_status"], "waiting_for_source_or_model")
        self.assertEqual(
            status["deploy_profile_sha256"], self.profile["_profile_sha256"]
        )
        self.assertIn("current_source", status["reason"])
        self.assertFalse((self.root / "issue_inbox").exists())

    def test_target_must_be_strictly_after_injected_local_today(self) -> None:
        target, generated = producer._target_and_times(
            date(2030, 1, 2),
            "2030-01-01T13:00:00Z",
            now=datetime(2030, 1, 1, 14, tzinfo=timezone.utc),
            timezone_name="Asia/Shanghai",
        )
        self.assertEqual(target, date(2030, 1, 2))
        self.assertEqual(generated, "2030-01-01T14:00:00.000000Z")
        with self.assertRaises(producer._NoIssuableTarget):
            producer._target_and_times(
                date(2030, 1, 2),
                "2030-01-01T13:00:00Z",
                now=datetime(2030, 1, 1, 16, tzinfo=timezone.utc),
                timezone_name="Asia/Shanghai",
            )

    def test_source_as_of_cannot_reach_target_start(self) -> None:
        with self.assertRaises(producer.IssueProducerInputError):
            producer._target_and_times(
                date(2030, 1, 2),
                "2030-01-01T16:00:00Z",
                now=datetime(2030, 1, 1, 15, tzinfo=timezone.utc),
                timezone_name="Asia/Shanghai",
            )

    def test_target_row_is_invisible_to_model_window(self) -> None:
        model_columns = [
            *self.profile["model"]["displacement_columns"],
            *self.profile["model"]["exogenous_columns"],
        ]
        dates = pd.date_range("2030-01-01", periods=8, freq="D")
        frame = pd.DataFrame(
            {
                "Date": dates,
                **{
                    column: [float(day) for day in range(8)] for column in model_columns
                },
            }
        )
        source = type("Source", (), {"frame": frame, "watermark": date(2030, 1, 7)})
        rows = producer._select_model_rows(source, self.profile)
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows["Date"].max().date(), date(2030, 1, 7))
        self.assertNotIn(date(2030, 1, 8), [value.date() for value in rows["Date"]])

    def test_current_watermark_displacement_must_equal_authoritative_state(
        self,
    ) -> None:
        live_order = self.profile["source_feed"]["station_order_live"]
        columns = [
            *self.profile["model"]["displacement_columns"],
            *self.profile["model"]["exogenous_columns"],
        ]
        frame = pd.DataFrame(
            {
                "Date": pd.date_range("2030-01-01", periods=7, freq="D"),
                **{column: [float(day) for day in range(7)] for column in columns},
            }
        )
        authoritative = {station: 6.0 for station in live_order}
        self.assertEqual(
            producer._validate_authoritative_latest_displacement(
                frame, self.profile, authoritative
            ),
            authoritative,
        )
        authoritative["ATU3"] = 5.999999999999999
        with self.assertRaisesRegex(producer._MachineWaiting, "ATU3"):
            producer._validate_authoritative_latest_displacement(
                frame, self.profile, authoritative
            )

    def test_predictions_are_mapped_by_station_name_into_live_order(self) -> None:
        live_order = self.profile["source_feed"]["station_order_live"]
        model_order = self.profile["source_feed"]["station_order_model"]
        latest = {station: 100.0 + index for index, station in enumerate(live_order)}
        predictions = {
            seed: {
                station: float(seed * 100 + model_index)
                for model_index, station in enumerate(reversed(model_order))
            }
            for seed in range(5)
        }
        rows = producer._station_rows(
            self.profile,
            latest_displacement=latest,
            seed_predictions=predictions,
        )
        self.assertEqual([row["station"] for row in rows], live_order)
        mj9 = next(row for row in rows if row["station"] == "MJ9")
        self.assertEqual(mj9["persistence_mm"], latest["MJ9"])
        self.assertEqual(mj9["seed3_p50_mm"], predictions[3]["MJ9"])

    def test_all_five_checkpoint_results_are_required(self) -> None:
        stations = self.profile["source_feed"]["station_order_live"]
        latest = {station: 1.0 for station in stations}
        predictions = {
            seed: {station: 2.0 for station in stations} for seed in range(4)
        }
        with self.assertRaisesRegex(producer.IssueProducerInputError, "Exactly five"):
            producer._station_rows(
                self.profile,
                latest_displacement=latest,
                seed_predictions=predictions,
            )

    def test_ledger_gate_rejects_an_arbitrary_existing_file(self) -> None:
        ledger = self.root / "ledger.sqlite3"
        status = self.root / "status.json"
        self.assertEqual(
            producer._required_target(
                source_watermark=date(2030, 1, 1),
                activation_watermark=date(2030, 1, 1),
                ledger_path=ledger,
                live_status_path=status,
            ),
            date(2030, 1, 2),
        )
        ledger.write_bytes(b"ledger")
        _write_json(
            status,
            {
                "schema_version": "ootang_prequential_live_status_v1",
                "next_target_date": "2030-01-02",
                "outstanding_target_date": None,
            },
        )
        live_profile = live.load_config()
        with self.assertRaisesRegex(
            producer.IssueProducerInputError, "complete replay"
        ):
            producer._required_target(
                source_watermark=date(2030, 1, 1),
                activation_watermark=date(2030, 1, 1),
                ledger_path=ledger,
                live_status_path=status,
                live_profile=live_profile,
                live_paths=live.runtime_paths(live_profile, runtime_root=self.root),
                prerequisites=object(),
            )

    def test_idempotency_preserves_first_bytes_across_capture_metadata(self) -> None:
        first_manifest = self._input_manifest("first.json", marker="first capture")
        second_manifest = self._input_manifest("second.json", marker="later capture")
        path = self.root / "issue_inbox" / "2030-01-02.json"
        first = self._issue_payload(first_manifest)
        self.assertTrue(self._publish(path, first))
        first_bytes = path.read_bytes()
        second = self._issue_payload(
            second_manifest,
            generated="2030-01-01T14:30:00Z",
            source_as_of="2030-01-01T13:30:00Z",
        )
        self.assertFalse(self._publish(path, second))
        self.assertEqual(path.read_bytes(), first_bytes)
        receipt = self.root / "issue_receipts" / "2030-01-02.json"
        self.assertTrue(receipt.is_file())
        receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
        exact = Path(receipt_payload["exact_issue_object"]["path"])
        self.assertEqual(exact.read_bytes(), first_bytes)

    def test_receipt_blocks_existing_issue_time_field_tamper(self) -> None:
        manifest = self._input_manifest("input.json")
        first = self._issue_payload(manifest)
        for field in ("generated_at_utc", "source_as_of_at_utc"):
            with self.subTest(field=field):
                base = self.root / field
                path = base / "issue_inbox" / "2030-01-02.json"
                receipt_root = base / "issue_receipts"
                object_root = base / "objects" / "sha256"
                producer._publish_issue_idempotently(
                    path,
                    first,
                    receipt_root=receipt_root,
                    object_root=object_root,
                )
                tampered = json.loads(path.read_text(encoding="utf-8"))
                tampered[field] = "2030-01-01T14:45:00Z"
                _write_json(path, tampered)
                retry = self._issue_payload(
                    manifest,
                    generated="2030-01-01T14:30:00Z",
                    source_as_of="2030-01-01T13:30:00Z",
                )
                with self.assertRaisesRegex(
                    producer.IssueProducerConflict, "first-publication receipt"
                ):
                    producer._publish_issue_idempotently(
                        path,
                        retry,
                        receipt_root=receipt_root,
                        object_root=object_root,
                    )

    def test_receipt_recovers_missing_inbox_with_exact_first_bytes(self) -> None:
        manifest = self._input_manifest("input.json")
        path = self.root / "issue_inbox" / "2030-01-02.json"
        first = self._issue_payload(manifest)
        self.assertTrue(self._publish(path, first))
        first_bytes = path.read_bytes()
        path.unlink()
        retry = self._issue_payload(
            manifest,
            generated="2030-01-01T14:30:00Z",
            source_as_of="2030-01-01T13:30:00Z",
        )
        self.assertTrue(self._publish(path, retry))
        self.assertEqual(path.read_bytes(), first_bytes)

    def test_receipt_restores_first_bytes_before_candidate_scientific_conflict(
        self,
    ) -> None:
        first_manifest = self._input_manifest("first-committed.json")
        changed_manifest = self._input_manifest(
            "changed-candidate.json", dataset_content=b"changed dataset\n"
        )
        path = self.root / "issue_inbox" / "2030-01-02.json"
        first = self._issue_payload(first_manifest)
        self.assertTrue(self._publish(path, first))
        first_bytes = path.read_bytes()
        path.unlink()

        with self.assertRaisesRegex(
            producer.IssueProducerConflict, "different semantic issue payload"
        ):
            self._publish(path, self._issue_payload(changed_manifest))

        self.assertEqual(path.read_bytes(), first_bytes)

    def test_receipt_time_binding_tamper_is_blocked(self) -> None:
        manifest = self._input_manifest("input.json")
        path = self.root / "issue_inbox" / "2030-01-02.json"
        payload = self._issue_payload(manifest)
        self._publish(path, payload)
        receipt_path = self.root / "issue_receipts" / "2030-01-02.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["generated_at_utc"] = "2030-01-01T14:45:00Z"
        _write_json(receipt_path, receipt)
        with self.assertRaisesRegex(
            producer.IssueProducerConflict, "receipt time binding"
        ):
            self._publish(path, payload)

    def test_same_target_changed_prediction_conflicts(self) -> None:
        manifest = self._input_manifest("input.json")
        path = self.root / "issue_inbox" / "2030-01-02.json"
        first = self._issue_payload(manifest)
        self._publish(path, first)
        changed = self._issue_payload(manifest)
        changed["stations"][0]["persistence_mm"] = 9.0
        with self.assertRaises(producer.IssueProducerConflict):
            self._publish(path, changed)

    def test_nested_input_manifest_tamper_is_detected(self) -> None:
        manifest = self._input_manifest("input.json")
        path = self.root / "issue_inbox" / "2030-01-02.json"
        payload = self._issue_payload(manifest)
        self._publish(path, payload)
        manifest.path.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(producer.IssueProducerConflict):
            self._publish(path, payload)

    def test_input_manifest_binds_issue_producer_implementation(self) -> None:
        manifest = self._input_manifest("input.json")
        payload = json.loads(manifest.path.read_text(encoding="utf-8"))
        implementation = payload["implementation"]
        self.assertEqual(
            set(implementation),
            {
                "schema_version",
                "deploy_profile",
                "issue_producer",
                "pyproject",
                "uv_lock",
                "runtime",
            },
        )
        self.assertEqual(
            implementation["deploy_profile"]["sha256"],
            self.profile["_profile_sha256"],
        )
        self.assertEqual(
            implementation["issue_producer"]["sha256"],
            _sha256(Path(producer.__file__)),
        )
        stable = producer._validate_implementation_record(implementation)
        self.assertEqual(
            stable["issue_producer_sha256"],
            implementation["issue_producer"]["sha256"],
        )
        self.assertEqual(
            set(implementation["runtime"]),
            {
                "python_implementation",
                "python_version",
                "numpy_version",
                "pandas_version",
                "torch_version",
            },
        )

    def test_implementation_record_tamper_is_recursively_blocked(self) -> None:
        for name in ("runtime_version", "producer_hash"):
            with self.subTest(name=name):
                manifest = self._input_manifest(f"implementation-{name}.json")
                value = json.loads(manifest.path.read_text(encoding="utf-8"))
                if name == "runtime_version":
                    value["implementation"]["runtime"]["python_version"] = "0.0.0"
                else:
                    value["implementation"]["issue_producer"]["sha256"] = "0" * 64
                _write_json(manifest.path, value)
                tampered = producer.Artifact(
                    manifest.path,
                    _sha256(manifest.path),
                    manifest.path.stat().st_size,
                )
                issue = self._issue_payload(tampered)
                with self.assertRaises(producer.IssueProducerConflict):
                    self._publish(self.root / "issue_inbox" / "2030-01-02.json", issue)

    def test_existing_outcome_contamination_is_rejected(self) -> None:
        manifest = self._input_manifest("input.json")
        path = self.root / "issue_inbox" / "2030-01-02.json"
        contaminated = self._issue_payload(manifest)
        contaminated["stations"][0]["outcome"] = 7.0
        _write_json(path, contaminated)
        clean = self._issue_payload(manifest)
        with self.assertRaises(producer.IssueProducerConflict):
            self._publish(path, clean)

    def test_nested_artifact_tamper_is_detected(self) -> None:
        artifact = self._artifact("nested/source.json", b"before\n")
        artifact.path.write_bytes(b"after\n")
        with self.assertRaises(producer.IssueProducerInputError):
            producer._coerce_artifact(artifact, name="nested source")


class E2ACompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-issue-live-compat-test-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = live.load_config()
        self.paths = live.runtime_paths(self.profile, runtime_root=self.root)
        self.stations = list(self.profile["stations"])

    def _artifact(self, name: str, content: bytes = b"{}\n") -> dict[str, object]:
        path = self.root / "objects" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "path": str(path.resolve()),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }

    def test_materialized_issue_loads_through_e2a_consumer(self) -> None:
        watermark = date(2030, 1, 1)
        source_path = self.root / "source_snapshot" / "manifest.json"
        latest = {station: 10.0 + index for index, station in enumerate(self.stations)}
        _write_json(
            source_path,
            {
                "schema_version": "ootang_live_source_snapshot_v1",
                "case": "ootang",
                "captured_at_utc": "2030-01-01T10:00:00Z",
                "maximum_complete_finalized_date": watermark.isoformat(),
                "stations": self.stations,
                "outcome_source_id": "source-v1",
                "data_manifest": self._artifact("source-data.json"),
                "latest_finalized_displacement_mm": latest,
            },
        )
        checkpoints = [
            {
                "seed": seed,
                "artifact": self._artifact(
                    f"checkpoint-{seed}.bin", f"seed={seed}\n".encode()
                ),
            }
            for seed in range(5)
        ]
        model_path = self.root / "model_bundle" / "manifest.json"
        _write_json(
            model_path,
            {
                "schema_version": "ootang_live_five_seed_model_bundle_v1",
                "case": "ootang",
                "model_version": "ootang-five-seed-test-v1",
                "created_at_utc": "2030-01-01T11:00:00Z",
                "training_cutoff_date": watermark.isoformat(),
                "stations": self.stations,
                "seeds": [0, 1, 2, 3, 4],
                "best_seed_selected": False,
                "target": {
                    "name": "next_natural_day_cumulative_displacement",
                    "unit": "mm",
                    "horizon": "P1D",
                },
                "input_schema_sha256": "d" * 64,
                "training_manifest": self._artifact("training.json"),
                "checkpoints": checkpoints,
            },
        )
        prerequisites = live.load_prerequisites(self.profile, self.paths)
        self.assertIsNotNone(prerequisites)
        input_manifest = self._artifact("input.json")
        predictions = {
            seed: {station: latest[station] + seed for station in self.stations}
            for seed in range(5)
        }
        deploy_profile = producer.load_config()
        station_rows = producer._station_rows(
            deploy_profile,
            latest_displacement=latest,
            seed_predictions=predictions,
        )
        issue_path = self.root / "issue_inbox" / "2030-01-02.json"
        _write_json(
            issue_path,
            {
                "schema_version": "ootang_live_issue_batch_v1",
                "target_date": "2030-01-02",
                "generated_at_utc": "2030-01-01T15:00:00Z",
                "source_as_of_at_utc": "2030-01-01T14:00:00Z",
                "source_snapshot_sha256": _sha256(source_path),
                "model_manifest_sha256": _sha256(model_path),
                "input_manifest": input_manifest,
                "stations": station_rows,
            },
        )
        parsed = live.load_issue_batch(issue_path, self.profile, prerequisites)
        self.assertEqual(parsed.target_date, date(2030, 1, 2))
        self.assertEqual(list(parsed.experts_by_station), self.stations)


class FullProducerIntegrationTests(unittest.TestCase):
    """Exercise source recursion, five safe checkpoints, and E2-A consumption."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="ootang-full-issue-producer-test-", dir=ROOT
        )
        cls.root = Path(cls._temporary.name)
        cls.profile = producer.load_config()
        incoming = cls.root / cls.profile["runtime"]["incoming_feed"]
        incoming.parent.mkdir(parents=True, exist_ok=True)
        stations = cls.profile["source_feed"]["station_order_live"]
        day = date(2020, 7, 1)
        record = {
            "schema_version": cls.profile["source_feed"]["record_schema_version"],
            "date": day.isoformat(),
            "revision_id": "integration-revision-1",
            "observed_at_utc": "2020-07-01T04:00:00Z",
            "available_at_utc": "2020-07-01T05:00:00Z",
            "finalized_at_utc": "2020-07-01T06:00:00Z",
            "finalized": True,
            "rainfall_mm": 3.0,
            "reservoir_water_level_m": 150.5,
            "displacement_mm": {
                station: 1000.0 + index for index, station in enumerate(stations)
            },
        }
        _write_json(
            incoming,
            {
                "schema_version": cls.profile["source_feed"]["schema_version"],
                "outcome_source_id": "integration-machine-source-v1",
                "exported_at_utc": "2020-07-01T07:00:00Z",
                "records": [record],
            },
        )
        ingested = source.ingest_source(
            cls.profile,
            runtime_root=cls.root,
            now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
        )
        assert ingested.source is not None
        with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
            bundle.build_model_bundle(
                ingested.source,
                cls.profile,
                runtime_root=cls.root,
                now=datetime(2020, 7, 1, 10, tzinfo=timezone.utc),
                _epochs_override=1,
            )
        live.poll_live_runner(
            runtime_root=cls.root,
            clock=lambda: datetime(2020, 7, 1, 14, 30, tzinfo=timezone.utc),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    @staticmethod
    def _test_bundle_loader(profile: dict[str, object], runtime_root: Path):
        return bundle.load_deploy_bundle(
            profile,
            runtime_root=runtime_root,
            _allow_test_epochs=True,
        )

    def _stash_target_publication(
        self,
    ) -> tuple[Path, Path, bytes | None, bytes | None]:
        issue_path = self.root / "issue_inbox" / "2020-07-02.json"
        receipt_path = self.root / "issue_receipts" / "2020-07-02.json"
        saved_issue = issue_path.read_bytes() if issue_path.is_file() else None
        saved_receipt = receipt_path.read_bytes() if receipt_path.is_file() else None
        if issue_path.exists():
            issue_path.unlink()
        if receipt_path.exists():
            receipt_path.unlink()
        return issue_path, receipt_path, saved_issue, saved_receipt

    @staticmethod
    def _restore_target_publication(
        issue_path: Path,
        receipt_path: Path,
        saved_issue: bytes | None,
        saved_receipt: bytes | None,
    ) -> None:
        if issue_path.exists():
            issue_path.unlink()
        if receipt_path.exists():
            receipt_path.unlink()
        if saved_issue is not None:
            issue_path.parent.mkdir(parents=True, exist_ok=True)
            issue_path.write_bytes(saved_issue)
        if saved_receipt is not None:
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_bytes(saved_receipt)

    def test_authoritative_displacement_mismatch_waits_before_prediction(self) -> None:
        current = source.load_current_source(
            self.profile, runtime_root=self.root, project_root=ROOT
        )
        changed_frame = current.frame.copy()
        changed_frame.loc[changed_frame.index[-1], "ATU3_disp"] += 1e-12
        changed_source = replace(current, frame=changed_frame)
        issue_path = self.root / "issue_inbox" / "2020-07-02.json"
        receipt_path = self.root / "issue_receipts" / "2020-07-02.json"
        before_issue = issue_path.read_bytes() if issue_path.is_file() else None
        before_receipt = receipt_path.read_bytes() if receipt_path.is_file() else None

        with (
            mock.patch.object(
                source, "load_current_source", return_value=changed_source
            ),
            mock.patch.object(bundle, "predict_p50") as predict,
            mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}),
        ):
            result = producer.produce_issue(
                runtime_root=self.root,
                clock=lambda: datetime(2020, 7, 1, 15, tzinfo=timezone.utc),
                _bundle_loader=self._test_bundle_loader,
            )

        self.assertEqual(result.status, "waiting_for_issuable_future_target")
        self.assertIn("ATU3", json.loads(result.status_path.read_text())["reason"])
        predict.assert_not_called()
        self.assertEqual(
            issue_path.read_bytes() if issue_path.is_file() else None, before_issue
        )
        self.assertEqual(
            receipt_path.read_bytes() if receipt_path.is_file() else None,
            before_receipt,
        )

    def test_consumer_invalid_candidate_creates_no_receipt_or_inbox(self) -> None:
        publication = self._stash_target_publication()
        issue_path, receipt_path, _, _ = publication
        try:
            with (
                mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}),
                mock.patch.object(
                    live,
                    "load_issue_batch",
                    side_effect=live.LiveInputError("injected consumer contract drift"),
                ) as consumer,
            ):
                with self.assertRaisesRegex(
                    producer.IssueProducerInputError,
                    "injected consumer contract drift",
                ):
                    producer.produce_issue(
                        runtime_root=self.root,
                        clock=lambda: datetime(2020, 7, 1, 15, 30, tzinfo=timezone.utc),
                        _bundle_loader=self._test_bundle_loader,
                    )
            self.assertEqual(consumer.call_count, 1)
            self.assertFalse(issue_path.exists())
            self.assertFalse(receipt_path.exists())
        finally:
            self._restore_target_publication(*publication)

    def test_clock_crossing_target_start_withdraws_just_published_inbox(self) -> None:
        publication = self._stash_target_publication()
        issue_path, receipt_path, _, _ = publication
        observations = iter(
            [
                datetime(2020, 7, 1, 15, 0, tzinfo=timezone.utc),
                datetime(2020, 7, 1, 15, 1, tzinfo=timezone.utc),
                datetime(2020, 7, 1, 15, 50, tzinfo=timezone.utc),
                datetime(2020, 7, 1, 15, 59, 59, tzinfo=timezone.utc),
                datetime(2020, 7, 1, 16, 0, tzinfo=timezone.utc),
            ]
        )
        observed: list[datetime] = []

        def advancing_clock() -> datetime:
            value = next(observations)
            observed.append(value)
            return value

        try:
            with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
                with self.assertRaisesRegex(
                    producer.IssueProducerInputError,
                    "did not complete before target start",
                ):
                    producer.produce_issue(
                        runtime_root=self.root,
                        clock=advancing_clock,
                        _bundle_loader=self._test_bundle_loader,
                    )
            self.assertEqual(len(observed), 5)
            self.assertFalse(issue_path.exists())
            self.assertTrue(receipt_path.is_file())
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["generated_at_utc"], "2020-07-01T15:50:00.000000Z")
            exact_object = Path(receipt["exact_issue_object"]["path"])
            self.assertTrue(exact_object.is_file())
            status = json.loads(
                (self.root / self.profile["runtime"]["issue_status"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(status["producer_status"], "blocked_integrity")
            self.assertEqual(status["recorded_at_utc"], "2020-07-01T16:00:00.000000Z")
        finally:
            self._restore_target_publication(*publication)

    def test_post_publication_consumer_failure_withdraws_new_inbox(self) -> None:
        publication = self._stash_target_publication()
        issue_path, receipt_path, _, _ = publication
        original_consumer = live.load_issue_batch
        calls = 0

        def fail_durable_validation(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_consumer(*args, **kwargs)
            raise live.LiveInputError("injected durable consumer failure")

        try:
            with (
                mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}),
                mock.patch.object(
                    live, "load_issue_batch", side_effect=fail_durable_validation
                ),
            ):
                with self.assertRaisesRegex(
                    producer.IssueProducerInputError,
                    "injected durable consumer failure",
                ):
                    producer.produce_issue(
                        runtime_root=self.root,
                        clock=lambda: datetime(2020, 7, 1, 15, 40, tzinfo=timezone.utc),
                        _bundle_loader=self._test_bundle_loader,
                    )
            self.assertEqual(calls, 2)
            self.assertFalse(issue_path.exists())
            self.assertTrue(receipt_path.is_file())
        finally:
            self._restore_target_publication(*publication)

    def test_default_producer_rejects_reduced_epoch_bundle(self) -> None:
        with self.assertRaisesRegex(
            producer.IssueProducerInputError, "Reduced-epoch checkpoint"
        ):
            producer.produce_issue(
                runtime_root=self.root,
                clock=lambda: datetime(2020, 7, 1, 14, 50, tzinfo=timezone.utc),
            )

    def test_machine_issue_replays_checkpoints_and_is_byte_idempotent(self) -> None:
        with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
            first = producer.produce_issue(
                runtime_root=self.root,
                clock=lambda: datetime(2020, 7, 1, 15, tzinfo=timezone.utc),
                _bundle_loader=self._test_bundle_loader,
            )
        self.assertEqual(first.status, "issued")
        self.assertEqual(first.target_date, date(2020, 7, 2))
        assert first.issue_path is not None
        original = first.issue_path.read_bytes()
        issue = json.loads(original)
        self.assertEqual(
            [row["station"] for row in issue["stations"]],
            self.profile["source_feed"]["station_order_live"],
        )
        for row in issue["stations"]:
            self.assertEqual(
                set(row),
                {
                    "station",
                    "persistence_mm",
                    "seed0_p50_mm",
                    "seed1_p50_mm",
                    "seed2_p50_mm",
                    "seed3_p50_mm",
                    "seed4_p50_mm",
                },
            )
        with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
            second = producer.produce_issue(
                runtime_root=self.root,
                clock=lambda: datetime(2020, 7, 1, 15, 10, tzinfo=timezone.utc),
                _bundle_loader=self._test_bundle_loader,
            )
        self.assertEqual(second.status, "already_issued_idempotent")
        self.assertEqual(first.issue_path.read_bytes(), original)
        producer_status = json.loads(second.status_path.read_text(encoding="utf-8"))
        self.assertEqual(
            producer_status["input_manifest_sha256"],
            issue["input_manifest"]["sha256"],
        )
        self.assertEqual(
            producer_status["exact_issue_object"]["sha256"],
            hashlib.sha256(original).hexdigest(),
        )
        self.assertEqual(
            Path(producer_status["issue_receipt"]["path"]).parent,
            (self.root / self.profile["runtime"]["issue_receipts"]).resolve(),
        )

        live_profile = live.load_config()
        prerequisites = live.load_prerequisites(
            live_profile, live.runtime_paths(live_profile, runtime_root=self.root)
        )
        assert prerequisites is not None
        parsed = live.load_issue_batch(first.issue_path, live_profile, prerequisites)
        self.assertEqual(parsed.target_date, date(2020, 7, 2))

    def test_runner_lock_prevents_ledger_to_publish_race(self) -> None:
        live_profile = live.load_config()
        paths = live.runtime_paths(live_profile, runtime_root=self.root)
        issue_status = self.root / self.profile["runtime"]["issue_status"]
        before = issue_status.read_bytes() if issue_status.is_file() else None
        paths.lock.parent.mkdir(parents=True, exist_ok=True)
        with paths.lock.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
                with self.assertRaises(producer.IssueProducerBusy):
                    producer.produce_issue(
                        runtime_root=self.root,
                        clock=lambda: datetime(2020, 7, 1, 15, 15, tzinfo=timezone.utc),
                        _bundle_loader=self._test_bundle_loader,
                    )
        after = issue_status.read_bytes() if issue_status.is_file() else None
        self.assertEqual(after, before)

    def test_verified_ledger_status_binding_rejects_tamper(self) -> None:
        live_profile = live.load_config()
        paths = live.runtime_paths(live_profile, runtime_root=self.root)
        original = paths.status.read_bytes()
        fields = {
            "ledger_event_count": 999,
            "ledger_terminal_sha256": "0" * 64,
            "ledger_terminal_sequence_id": 999,
            "next_target_date": "2020-07-03",
            "outstanding_target_date": "2020-07-02",
        }
        try:
            for field, tampered_value in fields.items():
                with self.subTest(field=field):
                    status = json.loads(original)
                    status[field] = tampered_value
                    _write_json(paths.status, status)
                    with mock.patch.dict(os.environ, {bundle.TEST_EPOCH_ENV: "1"}):
                        with self.assertRaisesRegex(
                            producer.IssueProducerInputError,
                            "verified ledger projection",
                        ):
                            producer.produce_issue(
                                runtime_root=self.root,
                                clock=lambda: datetime(
                                    2020, 7, 1, 15, 20, tzinfo=timezone.utc
                                ),
                                _bundle_loader=self._test_bundle_loader,
                            )
        finally:
            paths.status.write_bytes(original)

    def test_current_source_nested_tamper_blocks_and_refreshes_status(self) -> None:
        current = source.load_current_source(
            self.profile, runtime_root=self.root, project_root=ROOT
        )
        original = current.dataset.path.read_bytes()
        try:
            current.dataset.path.write_bytes(original + b"tamper")
            with self.assertRaises(producer.IssueProducerInputError):
                producer.produce_issue(
                    runtime_root=self.root,
                    clock=lambda: datetime(2020, 7, 1, 15, 20, tzinfo=timezone.utc),
                )
            status_path = self.root / self.profile["runtime"]["issue_status"]
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["producer_status"], "blocked_integrity")
        finally:
            current.dataset.path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
