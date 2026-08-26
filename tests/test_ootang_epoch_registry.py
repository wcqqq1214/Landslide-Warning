from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_registry as registry
from monitoring import ootang_live_source as source_module
from monitoring import ootang_prequential_live as live_module


FIXED_NOW = datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc)


class EpochRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = registry.load_registry_profile()

    @staticmethod
    def _record(path: Path, **extra: object) -> dict[str, object]:
        raw = path.read_bytes()
        return {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
            **extra,
        }

    def _fake_prebuilder(
        self,
        profile,
        _paths,
        live_root: Path,
        _now,
        feed_copy: Path,
    ) -> registry.CandidateBuild:
        feed_payload = json.loads(feed_copy.read_text(encoding="utf-8"))
        watermark = feed_payload["records"][-1]["date"]
        outcome_source_id = feed_payload["outcome_source_id"]
        artifacts = live_root / "objects" / "sha256"
        artifacts.mkdir(parents=True, exist_ok=True)
        source_rows = []
        for role, name in (
            ("current_source_pointer", "source-current.json"),
            ("activation_source_manifest", "activation.json"),
            ("semantic_source_manifest", "semantic.json"),
            ("canonical_source_dataset", "source.json"),
        ):
            path = artifacts / name
            path.write_bytes(f"{role}\n".encode())
            source_rows.append(self._record(path, role=role))
        feed_snapshot = artifacts / "daily-feed.json"
        feed_snapshot.write_bytes(feed_copy.read_bytes())
        source_rows.append(self._record(feed_snapshot, role="daily_feed_snapshot"))
        model = artifacts / "model.json"
        training = artifacts / "training.json"
        model.write_bytes(b"model\n")
        training.write_bytes(b"training\n")
        model_record = self._record(model)
        checkpoints = []
        for seed in range(5):
            checkpoint = artifacts / f"seed-{seed}.pt"
            checkpoint.write_bytes(f"checkpoint-{seed}\n".encode())
            checkpoints.append(self._record(checkpoint, seed=seed))
        self.assertTrue(feed_copy.is_file())
        live_binding = profile["upstream_profiles"]["live"]
        live_profile = json.loads(
            (Path(profile["_project_root"]) / live_binding["path"]).read_text(
                encoding="utf-8"
            )
        )
        live_epoch_id = registry._live_canonical_sha256(  # noqa: SLF001
            {
                "profile_id": live_profile["profile_id"],
                "profile_sha256": live_binding["expected_sha256"],
                "source_snapshot_sha256": next(
                    record["sha256"]
                    for record in source_rows
                    if record["role"] == "activation_source_manifest"
                ),
                "model_manifest_sha256": model_record["sha256"],
                "watermark": watermark,
                "implementation_sha256": "c" * 64,
                "environment_sha256": "d" * 64,
            }
        )
        return registry.CandidateBuild(
            live_epoch_id=live_epoch_id,
            watermark=watermark,
            outcome_source_id=outcome_source_id,
            source_artifacts=tuple(source_rows),
            model_manifest=model_record,
            training_manifest=self._record(training),
            checkpoints=tuple(checkpoints),
            algorithm_profile_sha256="b" * 64,
            implementation_sha256="c" * 64,
            environment_sha256="d" * 64,
        )

    @staticmethod
    def _feed_bytes(
        *,
        revision_id: str = "source-revision-one",
        exported_at_utc: str = "2026-08-26T14:00:00Z",
        value: float = 1.0,
    ) -> bytes:
        stations = ("ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9")
        payload = {
            "schema_version": "ootang_daily_finalized_feed_v1",
            "outcome_source_id": "machine-feed-v1",
            "exported_at_utc": exported_at_utc,
            "records": [
                {
                    "schema_version": "ootang_daily_finalized_record_v1",
                    "date": "2020-07-01",
                    "revision_id": revision_id,
                    "observed_at_utc": "2020-07-01T04:00:00Z",
                    "available_at_utc": "2020-07-01T05:00:00Z",
                    "finalized_at_utc": "2020-07-01T06:00:00Z",
                    "finalized": True,
                    "rainfall_mm": value,
                    "reservoir_water_level_m": 150.0 + value,
                    "displacement_mm": {
                        station: 1000.0 + value + index
                        for index, station in enumerate(stations)
                    },
                }
            ],
        }
        return (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode()

    def _poll(
        self,
        temporary: str,
        *,
        feed_bytes: bytes | None = None,
        _prebuilder=None,
    ):
        root = Path(temporary)
        feed = root / "candidate-feed.json"
        feed.write_bytes(feed_bytes or self._feed_bytes())
        runtime = root / "registry"
        path = registry._poll_epoch_registry(
            runtime_root=runtime,
            _source_feed_path=feed,
            clock=lambda: FIXED_NOW,
            _prebuilder=_prebuilder or self._fake_prebuilder,
        )
        profile = registry.load_registry_profile()
        paths = registry.registry_paths(
            profile, runtime_root=runtime, source_feed_path=feed
        )
        return path, profile, paths, feed

    def test_profile_binds_current_artifacts_and_r1_claims(self):
        self.assertEqual(
            self.profile["engineering_capabilities"], registry.EXPECTED_CAPABILITIES
        )
        self.assertEqual(self.profile["runtime"], registry.EXPECTED_RUNTIME)
        self.assertEqual(self.profile["protocol"], registry.EXPECTED_PROTOCOL)
        implementation = (
            Path(self.profile["_project_root"]) / self.profile["implementation"]["path"]
        )
        self.assertEqual(
            hashlib.sha256(implementation.read_bytes()).hexdigest(),
            self.profile["implementation"]["expected_sha256"],
        )
        required_capsule_files = {
            "code/convlstm/block_bootstrap.py",
            "code/convlstm/grid_interp.py",
            "code/monitoring/calibration_challengers.py",
            "code/warning/draft_evidence.py",
            "config/ootang_prequential_calibration_bakeoff.v1.json",
        }
        self.assertTrue(
            required_capsule_files.issubset(
                {binding["path"] for binding in self.profile["capsule_files"]}
            )
        )
        for forbidden in ("date", "freeze", "approve", "force", "backdate"):
            self.assertNotIn(forbidden, registry._parse_args([]).__dict__)  # noqa: SLF001

    def test_live_epoch_identity_exactly_matches_existing_live_formula(self):
        prerequisites = SimpleNamespace(
            source=SimpleNamespace(sha256="1" * 64, watermark=date(2026, 8, 26)),
            model=SimpleNamespace(sha256="2" * 64),
            implementation_sha256="3" * 64,
            environment_sha256="4" * 64,
        )
        live_profile = live_module.load_config()
        self.assertEqual(
            registry._live_epoch_id(self.profile, prerequisites),  # noqa: SLF001
            live_module._epoch_id(live_profile, prerequisites),  # noqa: SLF001
        )

    def test_missing_feed_is_machine_waiting_without_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            status_path = registry._poll_epoch_registry(
                runtime_root=root / "registry",
                _source_feed_path=root / "missing.json",
                clock=lambda: FIXED_NOW,
                _prebuilder=self._fake_prebuilder,
            )
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["registry_status"], "waiting_for_candidate_feed")
            self.assertEqual(status["event_count"], 0)
            self.assertIsNone(status["candidate"])
            self.assertFalse((root / "registry" / "events").exists())
            self.assertFalse((root / "registry" / "capsules").exists())
            for claim in registry.CLAIM_KEYS:
                self.assertFalse(status[claim])

    def test_invalid_future_feed_cannot_poison_the_observation_watermark(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            future = json.loads(self._feed_bytes())
            future["exported_at_utc"] = "9999-08-26T14:00:00Z"
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                self._poll(
                    temporary,
                    feed_bytes=(
                        json.dumps(future, separators=(",", ":")) + "\n"
                    ).encode(),
                )
            runtime = root / "registry"
            self.assertFalse((runtime / "feed_observations").exists())

            feed = root / "candidate-feed.json"
            feed.write_bytes(self._feed_bytes())
            status_path = registry._poll_epoch_registry(
                runtime_root=runtime,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=self._fake_prebuilder,
            )
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["feed_observation_count"], 1)

    def test_nonfinalized_feed_is_rejected_before_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = json.loads(self._feed_bytes())
            payload["records"][0]["finalized"] = False
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                self._poll(
                    temporary,
                    feed_bytes=(
                        json.dumps(payload, separators=(",", ":")) + "\n"
                    ).encode(),
                )
            self.assertFalse(
                (Path(temporary) / "registry" / "feed_observations").exists()
            )

    def test_overflowing_numeric_feed_is_cleanly_rejected_before_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = json.loads(self._feed_bytes())
            payload["records"][0]["rainfall_mm"] = 10**4000
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                self._poll(
                    temporary,
                    feed_bytes=(
                        json.dumps(payload, separators=(",", ":")) + "\n"
                    ).encode(),
                )
            self.assertFalse(
                (Path(temporary) / "registry" / "feed_observations").exists()
            )

    def test_candidate_ready_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            status_path, profile, paths, feed = self._poll(temporary)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(
                status["registry_status"], "immutable_candidate_record_ready"
            )
            self.assertEqual(status["feed_observation_count"], 1)
            self.assertEqual(status["event_count"], 1)
            self.assertIsNotNone(status["candidate"])
            for claim in registry.CLAIM_KEYS:
                self.assertFalse(status[claim])
            events = registry.replay_registry(profile, paths)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "candidate_ready")
            self.assertEqual(events[0]["previous_entry_sha256"], registry.ZERO_HASH)
            capsule_files = list(paths.capsules.glob("*.json"))
            receipt_files = list(paths.candidate_receipts.glob("*.json"))
            object_files = list(paths.objects.iterdir())
            self.assertEqual(len(capsule_files), 1)
            self.assertEqual(len(receipt_files), 1)
            self.assertGreater(len(object_files), 30)
            first_event = events[0].copy()
            first_receipt = receipt_files[0].read_bytes()

            rerun = registry._poll_epoch_registry(
                runtime_root=paths.root,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=mock.Mock(
                    side_effect=AssertionError(
                        "registered stable slot must not be rebuilt"
                    )
                ),
            )
            rerun_status = json.loads(rerun.read_text(encoding="utf-8"))
            self.assertEqual(rerun_status["event_count"], 1)
            self.assertEqual(registry.replay_registry(profile, paths)[0], first_event)
            self.assertEqual(receipt_files[0].read_bytes(), first_receipt)

    def test_receipt_published_before_event_is_recovered_without_rebuild(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                registry,
                "_append_candidate_event",
                side_effect=registry.EpochRegistryIntegrityError("simulated crash"),
            ):
                with self.assertRaises(registry.EpochRegistryIntegrityError):
                    self._poll(temporary)

            root = Path(temporary)
            runtime = root / "registry"
            feed = root / "candidate-feed.json"
            receipt_files = list(
                (runtime / "candidate_receipts" / "sha256").glob("*.json")
            )
            self.assertEqual(len(receipt_files), 1)
            self.assertFalse((runtime / "events").exists())

            status_path = registry._poll_epoch_registry(
                runtime_root=runtime,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=mock.Mock(
                    side_effect=AssertionError("orphan receipt must be recovered")
                ),
            )
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(
                status["registry_status"], "immutable_candidate_record_ready"
            )
            self.assertEqual(status["event_count"], 1)

    def test_orphan_recovery_rejects_a_changed_stable_slot_feed(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                registry,
                "_append_candidate_event",
                side_effect=registry.EpochRegistryIntegrityError("simulated crash"),
            ):
                with self.assertRaises(registry.EpochRegistryIntegrityError):
                    self._poll(temporary)

            root = Path(temporary)
            runtime = root / "registry"
            receipt_path = next(
                (runtime / "candidate_receipts" / "sha256").glob("*.json")
            )
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            Path(receipt["feed_slot_path"]).write_bytes(b"changed feed\n")
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry._poll_epoch_registry(
                    runtime_root=runtime,
                    _source_feed_path=root / "candidate-feed.json",
                    clock=lambda: FIXED_NOW,
                    _prebuilder=mock.Mock(
                        side_effect=AssertionError("orphan must not be rebuilt")
                    ),
                )
            self.assertFalse((runtime / "events").exists())

    def test_stale_orphan_cannot_override_a_newer_registered_feed(self):
        with tempfile.TemporaryDirectory() as temporary:
            first_feed = self._feed_bytes()
            with mock.patch.object(
                registry,
                "_append_candidate_event",
                side_effect=registry.EpochRegistryIntegrityError("simulated crash"),
            ):
                with self.assertRaises(registry.EpochRegistryIntegrityError):
                    self._poll(temporary, feed_bytes=first_feed)

            root = Path(temporary)
            feed = root / "candidate-feed.json"
            runtime = root / "registry"
            feed.write_bytes(
                self._feed_bytes(
                    revision_id="source-revision-two",
                    exported_at_utc="2026-08-26T14:30:00Z",
                    value=2.0,
                )
            )
            registry._poll_epoch_registry(
                runtime_root=runtime,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=self._fake_prebuilder,
            )
            profile = registry.load_registry_profile()
            paths = registry.registry_paths(
                profile, runtime_root=runtime, source_feed_path=feed
            )
            self.assertEqual(len(registry.replay_registry(profile, paths)), 1)
            event_bytes = {
                path.name: path.read_bytes() for path in paths.events.iterdir()
            }

            feed.write_bytes(first_feed)
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry._poll_epoch_registry(
                    runtime_root=runtime,
                    _source_feed_path=feed,
                    clock=lambda: FIXED_NOW,
                    _prebuilder=mock.Mock(
                        side_effect=AssertionError(
                            "stale orphan must not be recovered or rebuilt"
                        )
                    ),
                )
            self.assertEqual(
                {path.name: path.read_bytes() for path in paths.events.iterdir()},
                event_bytes,
            )

    def test_newer_orphan_observation_blocks_return_to_registered_tip(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, feed = self._poll(temporary)
            first_feed = feed.read_bytes()
            feed.write_bytes(
                self._feed_bytes(
                    revision_id="source-revision-two",
                    exported_at_utc="2026-08-26T14:30:00Z",
                    value=2.0,
                )
            )
            with mock.patch.object(
                registry,
                "_append_candidate_event",
                side_effect=registry.EpochRegistryIntegrityError("simulated crash"),
            ):
                with self.assertRaises(registry.EpochRegistryIntegrityError):
                    registry._poll_epoch_registry(
                        runtime_root=paths.root,
                        _source_feed_path=feed,
                        clock=lambda: FIXED_NOW,
                        _prebuilder=self._fake_prebuilder,
                    )
            self.assertEqual(len(registry.replay_registry(profile, paths)), 1)
            observations, _ = registry.replay_feed_observations(profile, paths)
            self.assertEqual(len(observations), 2)
            event_bytes = {
                path.name: path.read_bytes() for path in paths.events.iterdir()
            }

            feed.write_bytes(first_feed)
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry._poll_epoch_registry(
                    runtime_root=paths.root,
                    _source_feed_path=feed,
                    clock=lambda: FIXED_NOW,
                    _prebuilder=mock.Mock(
                        side_effect=AssertionError(
                            "rollback must fail before registered-tip recovery"
                        )
                    ),
                )
            self.assertEqual(
                {path.name: path.read_bytes() for path in paths.events.iterdir()},
                event_bytes,
            )

    def test_observation_event_is_authoritative_before_derived_feed_object(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, feed = self._poll(temporary)
            first_feed = feed.read_bytes()
            second_feed = self._feed_bytes(
                revision_id="source-revision-two",
                exported_at_utc="2026-08-26T14:30:00Z",
                value=2.0,
            )
            second_sha = hashlib.sha256(second_feed).hexdigest()
            feed.write_bytes(second_feed)
            real_publish = registry._publish_once  # noqa: SLF001

            def crash_before_derived_object(path, raw, *, root, name):
                if (
                    name == "candidate finalized feed snapshot"
                    and hashlib.sha256(raw).hexdigest() == second_sha
                ):
                    raise registry.EpochRegistryIntegrityError("simulated crash")
                return real_publish(path, raw, root=root, name=name)

            with (
                mock.patch.object(
                    registry, "_publish_once", side_effect=crash_before_derived_object
                ),
                self.assertRaises(registry.EpochRegistryIntegrityError),
            ):
                registry._poll_epoch_registry(
                    runtime_root=paths.root,
                    _source_feed_path=feed,
                    clock=lambda: FIXED_NOW,
                    _prebuilder=self._fake_prebuilder,
                )
            observations, semantics = registry.replay_feed_observations(profile, paths)
            self.assertEqual(len(observations), 2)
            self.assertEqual(semantics[-1].sha256, second_sha)
            self.assertFalse((paths.objects / second_sha).exists())

            feed.write_bytes(first_feed)
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry._poll_epoch_registry(
                    runtime_root=paths.root,
                    _source_feed_path=feed,
                    clock=lambda: FIXED_NOW,
                    _prebuilder=self._fake_prebuilder,
                )
            self.assertEqual(len(registry.replay_registry(profile, paths)), 1)

    def test_waiting_build_still_advances_the_feed_rollback_watermark(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, feed = self._poll(temporary)
            first_feed = feed.read_bytes()
            feed.write_bytes(
                self._feed_bytes(
                    revision_id="source-revision-two",
                    exported_at_utc="2026-08-26T14:30:00Z",
                    value=2.0,
                )
            )
            status_path = registry._poll_epoch_registry(
                runtime_root=paths.root,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=lambda *_args: None,
            )
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["registry_status"], "waiting_for_candidate_source")
            self.assertEqual(status["feed_observation_count"], 2)
            self.assertEqual(len(registry.replay_registry(profile, paths)), 1)

            feed.write_bytes(first_feed)
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry._poll_epoch_registry(
                    runtime_root=paths.root,
                    _source_feed_path=feed,
                    clock=lambda: FIXED_NOW,
                    _prebuilder=self._fake_prebuilder,
                )

    def test_feed_observation_head_detects_deleted_waiting_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, feed = self._poll(temporary)
            feed.write_bytes(
                self._feed_bytes(
                    revision_id="source-revision-two",
                    exported_at_utc="2026-08-26T14:30:00Z",
                    value=2.0,
                )
            )
            registry._poll_epoch_registry(
                runtime_root=paths.root,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=lambda *_args: None,
            )
            observations = sorted(paths.feed_observations.iterdir())
            self.assertEqual(len(observations), 2)
            observations[-1].unlink()
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry.replay_feed_observations(profile, paths)

    def test_crash_temp_namespace_is_not_enumerated_as_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, feed = self._poll(temporary)
            leftover = paths.root / ".tmp" / ".interrupted.deadbeef.create"
            leftover.write_bytes(b"unlinked crash temporary\n")
            registry._poll_epoch_registry(
                runtime_root=paths.root,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=self._fake_prebuilder,
            )
            self.assertFalse(leftover.exists())
            self.assertEqual(len(registry.replay_registry(profile, paths)), 1)
            self.assertEqual(
                [path.name for path in paths.events.iterdir()],
                ["00000000000000000001.json"],
            )
            self.assertTrue(
                all(
                    not path.name.endswith((".create", ".cache"))
                    for path in paths.candidate_receipts.iterdir()
                )
            )

    def test_crash_temp_hardlink_alias_is_machine_cleaned(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, paths, feed = self._poll(temporary)
            event = next(paths.events.iterdir())
            alias = paths.root / ".tmp" / ".event.deadbeef.create"
            alias.hardlink_to(event)
            self.assertEqual(event.stat().st_nlink, 2)
            registry._poll_epoch_registry(
                runtime_root=paths.root,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=self._fake_prebuilder,
            )
            self.assertFalse(alias.exists())
            self.assertEqual(event.stat().st_nlink, 1)

    def test_default_prebuilder_resamples_machine_time_and_maps_upstream_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            live_root = Path(temporary) / "live"
            feed = live_root / "incoming" / "daily_finalized_feed.json"
            feed.parent.mkdir(parents=True)
            feed.write_text("{}\n", encoding="utf-8")
            paths = registry.registry_paths(
                self.profile,
                runtime_root=Path(temporary) / "registry",
                source_feed_path=feed,
            )
            with (
                mock.patch.object(
                    source_module, "load_deploy_profile", return_value={}
                ),
                mock.patch.object(
                    source_module,
                    "ingest_source",
                    side_effect=source_module.SourceBusyError("busy"),
                ) as ingest,
            ):
                with self.assertRaises(registry.EpochRegistryBusyError):
                    registry._default_prebuilder(  # noqa: SLF001
                        self.profile, paths, live_root, FIXED_NOW, feed
                    )
                self.assertIsNone(ingest.call_args.kwargs["now"])

            with mock.patch.object(
                source_module,
                "load_deploy_profile",
                side_effect=source_module.SourceIntegrityError("tampered"),
            ):
                with self.assertRaises(registry.EpochRegistryIntegrityError):
                    registry._default_prebuilder(  # noqa: SLF001
                        self.profile, paths, live_root, FIXED_NOW, feed
                    )

    def test_worktree_change_during_prebuild_never_publishes_an_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            real_capture = registry._capture_capsule  # noqa: SLF001
            calls = 0

            def racing_capture(*args, **kwargs):
                nonlocal calls
                calls += 1
                manifest, snapshot = real_capture(*args, **kwargs)
                if calls == 2:
                    manifest = {**manifest, "tree_sha256": "f" * 64}
                return manifest, snapshot

            with (
                mock.patch.object(
                    registry, "_capture_capsule", side_effect=racing_capture
                ),
                self.assertRaises(registry.EpochRegistryIntegrityError),
            ):
                self._poll(temporary)
            self.assertFalse((Path(temporary) / "registry" / "events").exists())

    def test_profile_change_before_capsule_capture_never_publishes_a_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            real_read = registry._read_regular  # noqa: SLF001
            profile_reads = 0

            def racing_read(path, *args, **kwargs):
                nonlocal profile_reads
                snapshot = real_read(path, *args, **kwargs)
                if Path(path) == registry.DEFAULT_CONFIG_PATH:
                    profile_reads += 1
                    if profile_reads == 2:
                        raw = snapshot.raw + b" "
                        return registry.ArtifactSnapshot(
                            path=snapshot.path,
                            raw=raw,
                            sha256=hashlib.sha256(raw).hexdigest(),
                            size_bytes=len(raw),
                        )
                return snapshot

            with (
                mock.patch.object(registry, "_read_regular", side_effect=racing_read),
                self.assertRaises(registry.EpochRegistryIntegrityError),
            ):
                self._poll(temporary)
            runtime = Path(temporary) / "registry"
            self.assertFalse((runtime / "candidate_receipts").exists())
            self.assertFalse((runtime / "events").exists())

    def test_changed_feed_builds_a_distinct_sequential_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, feed = self._poll(temporary)
            first_feed = feed.read_bytes()
            feed.write_bytes(
                self._feed_bytes(
                    revision_id="source-revision-two",
                    exported_at_utc="2026-08-26T14:30:00Z",
                    value=2.0,
                )
            )
            registry._poll_epoch_registry(
                runtime_root=paths.root,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=self._fake_prebuilder,
            )
            events = registry.replay_registry(profile, paths)
            self.assertEqual(len(events), 2)
            self.assertNotEqual(events[0]["candidate_id"], events[1]["candidate_id"])
            self.assertNotEqual(events[0]["slot_id"], events[1]["slot_id"])
            self.assertEqual(
                events[1]["previous_entry_sha256"], events[0]["entry_sha256"]
            )

            event_bytes = {
                path.name: path.read_bytes() for path in paths.events.iterdir()
            }
            feed.write_bytes(first_feed)
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry._poll_epoch_registry(
                    runtime_root=paths.root,
                    _source_feed_path=feed,
                    clock=lambda: FIXED_NOW,
                    _prebuilder=mock.Mock(
                        side_effect=AssertionError("rollback must precede rebuild")
                    ),
                )
            self.assertEqual(
                {path.name: path.read_bytes() for path in paths.events.iterdir()},
                event_bytes,
            )

    def test_waiting_prebuilder_never_registers_a_partial_slot(self):
        with tempfile.TemporaryDirectory() as temporary:
            status_path, profile, paths, _ = self._poll(
                temporary, _prebuilder=lambda *_args: None
            )
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(status["registry_status"], "waiting_for_candidate_source")
            self.assertEqual(status["event_count"], 0)
            self.assertEqual(registry.replay_registry(profile, paths), ())
            self.assertEqual(len(list(paths.slots.iterdir())), 1)
            self.assertEqual(len(list(paths.capsules.glob("*.json"))), 1)

    def test_event_and_capsule_object_tampering_fail_closed(self):
        for target in ("event", "object"):
            with (
                self.subTest(target=target),
                tempfile.TemporaryDirectory() as temporary,
            ):
                _, profile, paths, _ = self._poll(temporary)
                if target == "event":
                    event = next(paths.events.iterdir())
                    payload = json.loads(event.read_text(encoding="utf-8"))
                    payload["candidate_id"] = "f" * 64
                    event.write_bytes(registry._canonical_bytes(payload))  # noqa: SLF001
                else:
                    next(paths.objects.iterdir()).write_bytes(b"tampered\n")
                with self.assertRaises(registry.EpochRegistryIntegrityError):
                    registry.replay_registry(profile, paths)

    def test_boolean_event_sequences_and_float_capsule_size_fail_closed(self):
        for target in ("observation", "candidate"):
            with (
                self.subTest(target=target),
                tempfile.TemporaryDirectory() as temporary,
            ):
                _, profile, paths, _ = self._poll(temporary)
                path = (
                    next(paths.feed_observations.iterdir())
                    if target == "observation"
                    else next(paths.events.iterdir())
                )
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["sequence_id"] = True
                payload["entry_sha256"] = hashlib.sha256(
                    registry._canonical_bytes(  # noqa: SLF001
                        registry._event_unsigned(payload)  # noqa: SLF001
                    )
                ).hexdigest()
                path.write_bytes(registry._canonical_bytes(payload))  # noqa: SLF001
                with self.assertRaises(registry.EpochRegistryIntegrityError):
                    if target == "observation":
                        registry.replay_feed_observations(profile, paths)
                    else:
                        registry.replay_registry(profile, paths)

        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, _ = self._poll(temporary)
            event = registry.replay_registry(profile, paths)[0]
            receipt = json.loads(
                (paths.root / event["candidate_receipt"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            capsule = dict(receipt["capsule"])
            capsule["size_bytes"] = float(capsule["size_bytes"])
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry._verify_capsule(profile, paths, capsule)  # noqa: SLF001

    def test_historical_replay_uses_snapshots_after_legal_slot_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, _ = self._poll(temporary)
            event = registry.replay_registry(profile, paths)[0]
            receipt_ref = event["candidate_receipt"]
            receipt = json.loads(
                (paths.root / receipt_ref["path"]).read_text(encoding="utf-8")
            )
            Path(receipt["candidate_build"]["model_manifest"]["path"]).write_bytes(
                b"runtime may replace current materialization after activation\n"
            )
            (Path(receipt["slot_live_root"]) / "ledger.sqlite3").write_bytes(
                b"future R2 namespace\n"
            )
            self.assertEqual(len(registry.replay_registry(profile, paths)), 1)

    def test_historical_capsule_survives_a_registry_implementation_upgrade(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, _ = self._poll(temporary)
            future_profile = {
                **profile,
                "implementation": {
                    **profile["implementation"],
                    "expected_sha256": "f" * 64,
                },
            }
            self.assertEqual(
                len(registry.replay_registry(future_profile, paths)),
                1,
            )

    def test_candidate_snapshot_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, _ = self._poll(temporary)
            event = registry.replay_registry(profile, paths)[0]
            receipt_ref = event["candidate_receipt"]
            receipt = json.loads(
                (paths.root / receipt_ref["path"]).read_text(encoding="utf-8")
            )
            snapshot = receipt["candidate_artifact_snapshots"][0]
            (paths.root / snapshot["object_path"]).write_bytes(b"tampered snapshot\n")
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry.replay_registry(profile, paths)

    def test_candidate_namespace_must_be_empty_at_commit(self):
        def polluted(profile, paths, live_root, now, feed_copy):
            build = self._fake_prebuilder(profile, paths, live_root, now, feed_copy)
            (live_root / "ledger.sqlite3").write_bytes(b"premature namespace\n")
            return build

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                self._poll(temporary, _prebuilder=polluted)

    def test_candidate_is_reverified_immediately_before_event_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            real_append = registry._append_candidate_event  # noqa: SLF001

            def race_namespace(profile, paths, events, receipt_snapshot, receipt):
                (Path(receipt["slot_live_root"]) / "ledger.sqlite3").write_bytes(
                    b"concurrent unauthorized namespace\n"
                )
                return real_append(profile, paths, events, receipt_snapshot, receipt)

            with (
                mock.patch.object(
                    registry,
                    "_append_candidate_event",
                    side_effect=race_namespace,
                ),
                self.assertRaises(registry.EpochRegistryIntegrityError),
            ):
                self._poll(temporary)
            self.assertFalse((Path(temporary) / "registry" / "events").exists())

    def test_deleted_head_is_rebuilt_but_ahead_head_blocks_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, profile, paths, feed = self._poll(temporary)
            paths.head.unlink()
            registry._poll_epoch_registry(
                runtime_root=paths.root,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=self._fake_prebuilder,
            )
            self.assertTrue(paths.head.is_file())
            head = json.loads(paths.head.read_text(encoding="utf-8"))
            head["sequence_id"] = 2
            paths.head.write_bytes(registry._canonical_bytes(head))  # noqa: SLF001
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry.replay_registry(profile, paths)

    def test_event_gap_branch_and_head_without_events_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "registry"
            paths = registry.registry_paths(
                self.profile,
                runtime_root=runtime,
                source_feed_path=root / "missing.json",
            )
            paths.events.mkdir(parents=True)
            (paths.events / "00000000000000000002.json").write_text(
                "{}\n", encoding="utf-8"
            )
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry.replay_registry(self.profile, paths)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "registry"
            paths = registry.registry_paths(
                self.profile,
                runtime_root=runtime,
                source_feed_path=root / "missing.json",
            )
            paths.root.mkdir(parents=True)
            paths.head.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry.replay_registry(self.profile, paths)

    def test_symlink_feed_and_manager_lock_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "real-feed.json"
            target.write_text("{}\n", encoding="utf-8")
            link = root / "feed.json"
            link.symlink_to(target)
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry._poll_epoch_registry(
                    runtime_root=root / "registry",
                    _source_feed_path=link,
                    clock=lambda: FIXED_NOW,
                    _prebuilder=self._fake_prebuilder,
                )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "registry"
            runtime.mkdir()
            (root / "lock-target").write_bytes(b"")
            (runtime / "manager.lock").symlink_to(root / "lock-target")
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry._poll_epoch_registry(
                    runtime_root=runtime,
                    _source_feed_path=root / "missing.json",
                    clock=lambda: FIXED_NOW,
                    _prebuilder=self._fake_prebuilder,
                )

    def test_symlinked_runtime_parent_and_candidate_artifact_parent_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "registry"
            runtime.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (runtime / "objects").symlink_to(outside, target_is_directory=True)
            feed = root / "feed.json"
            feed.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                registry._poll_epoch_registry(
                    runtime_root=runtime,
                    _source_feed_path=feed,
                    clock=lambda: FIXED_NOW,
                    _prebuilder=self._fake_prebuilder,
                )

        def symlinked_artifact_prebuilder(profile, paths, live_root, now, feed_copy):
            build = self._fake_prebuilder(profile, paths, live_root, now, feed_copy)
            real_models = live_root / "real-models"
            real_models.mkdir()
            outside_model = real_models / "model.json"
            outside_model.write_bytes(b"aliased\n")
            alias = live_root / "alias"
            alias.symlink_to(real_models, target_is_directory=True)
            model_record = self._record(outside_model)
            model_record["path"] = str(alias / "model.json")
            return registry.CandidateBuild(
                **{
                    **build.__dict__,
                    "model_manifest": model_record,
                }
            )

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                self._poll(
                    temporary,
                    _prebuilder=symlinked_artifact_prebuilder,
                )

    def test_concurrent_manager_is_busy_and_does_not_append(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            feed = root / "feed.json"
            feed.write_text("{}\n", encoding="utf-8")
            runtime = root / "registry"
            paths = registry.registry_paths(
                self.profile, runtime_root=runtime, source_feed_path=feed
            )
            handle = registry._acquire_manager_lock(paths)  # noqa: SLF001
            try:
                with self.assertRaises(registry.EpochRegistryBusyError):
                    registry._poll_epoch_registry(
                        runtime_root=runtime,
                        _source_feed_path=feed,
                        clock=lambda: FIXED_NOW,
                        _prebuilder=self._fake_prebuilder,
                    )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self.assertFalse(paths.events.exists())

    def test_five_seed_and_source_lineage_contracts_fail_closed(self):
        def incomplete(*args):
            build = self._fake_prebuilder(*args)
            return registry.CandidateBuild(
                **{
                    **build.__dict__,
                    "checkpoints": build.checkpoints[:-1],
                }
            )

        def missing_source(*args):
            build = self._fake_prebuilder(*args)
            return registry.CandidateBuild(
                **{
                    **build.__dict__,
                    "source_artifacts": build.source_artifacts[:3],
                }
            )

        for prebuilder in (incomplete, missing_source):
            with (
                self.subTest(_prebuilder=prebuilder.__name__),
                tempfile.TemporaryDirectory() as temporary,
            ):
                with self.assertRaises(registry.EpochRegistryIntegrityError):
                    self._poll(temporary, _prebuilder=prebuilder)

    def test_candidate_source_must_bind_the_exact_registry_feed(self):
        def mismatched_feed(*args):
            build = self._fake_prebuilder(*args)
            rows = list(build.source_artifacts)
            live_root = args[2]
            alternate = live_root / "objects" / "sha256" / "other-feed.json"
            alternate.write_bytes(
                self._feed_bytes(
                    revision_id="unbound-revision",
                    exported_at_utc="2026-08-26T14:30:00Z",
                    value=9.0,
                )
            )
            rows = [
                self._record(alternate, role="daily_feed_snapshot")
                if row["role"] == "daily_feed_snapshot"
                else row
                for row in rows
            ]
            return registry.CandidateBuild(
                **{**build.__dict__, "source_artifacts": tuple(rows)}
            )

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(registry.EpochRegistryIntegrityError):
                self._poll(temporary, _prebuilder=mismatched_feed)

    def test_status_is_only_a_cache_and_false_claims_are_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            status_path, _, paths, feed = self._poll(temporary)
            hostile = json.loads(status_path.read_text(encoding="utf-8"))
            hostile["automatic_epoch_rotation_implemented"] = True
            hostile["formal_warning_output"] = True
            status_path.write_bytes(registry._canonical_bytes(hostile))  # noqa: SLF001
            registry._poll_epoch_registry(
                runtime_root=paths.root,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=self._fake_prebuilder,
            )
            repaired = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertFalse(repaired["automatic_epoch_rotation_implemented"])
            self.assertFalse(repaired["formal_warning_output"])

    def test_only_the_reviewed_profile_path_is_accepted(self):
        raw = json.loads(
            (ROOT / "config" / "ootang_epoch_registry.v1.json").read_text(
                encoding="utf-8"
            )
        )
        raw["runtime"]["events"] = "../elsewhere"
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            hostile = Path(temporary) / "hostile.json"
            hostile.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(registry.EpochRegistryConfigError):
                registry.load_registry_profile(hostile)
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            copied = Path(temporary) / "copied.json"
            copied.write_bytes(registry.DEFAULT_CONFIG_PATH.read_bytes())
            with self.assertRaises(registry.EpochRegistryConfigError):
                registry.load_registry_profile(copied)

    def test_duplicate_json_keys_fail_closed(self):
        profile_text = (ROOT / "config" / "ootang_epoch_registry.v1.json").read_text(
            encoding="utf-8"
        )
        duplicate = profile_text.replace(
            '"schema_version": "ootang_epoch_registry_profile_v1",',
            '"schema_version": "ootang_epoch_registry_profile_v1",\n'
            '  "schema_version": "ootang_epoch_registry_profile_v1",',
            1,
        )
        with self.assertRaises(registry.EpochRegistryIntegrityError):
            registry._decode_json(  # noqa: SLF001
                duplicate.encode(), name="duplicate registry profile"
            )

    def test_injected_inputs_require_an_isolated_runtime(self):
        with self.assertRaises(registry.EpochRegistryIntegrityError):
            registry._poll_epoch_registry(_prebuilder=self._fake_prebuilder)
        with self.assertRaises(registry.EpochRegistryIntegrityError):
            registry._poll_epoch_registry(_source_feed_path=Path("feed.json"))
        with self.assertRaises(TypeError):
            registry.poll_epoch_registry(  # type: ignore[call-arg]
                runtime_root=registry.ROOT / "runtime" / "ootang_epoch_registry_v1"
            )
        with self.assertRaises(registry.EpochRegistryIntegrityError):
            registry._poll_epoch_registry(
                runtime_root=(registry.ROOT / "runtime" / "ootang_epoch_registry_v1"),
                _source_feed_path=Path("feed.json"),
                _prebuilder=self._fake_prebuilder,
            )

    def test_cli_maps_busy_and_integrity_without_manual_switches(self):
        parser = registry._parse_args  # noqa: SLF001
        with self.assertRaises(SystemExit):
            parser(["--date", "2026-08-27"])
        with mock.patch.object(
            registry,
            "poll_epoch_registry",
            side_effect=registry.EpochRegistryBusyError("busy"),
        ):
            self.assertEqual(registry.main([]), 3)
        with mock.patch.object(
            registry,
            "poll_epoch_registry",
            side_effect=registry.EpochRegistryIntegrityError("tamper"),
        ):
            self.assertEqual(registry.main([]), 2)


if __name__ == "__main__":
    unittest.main()
