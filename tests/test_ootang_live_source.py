"""Contracts for strict, content-addressed Ootang E2-B source ingestion."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
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

from monitoring import ootang_live_source as source  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ootang-source-test-", dir=ROOT
        )
        self.root = Path(self.temporary.name)
        self.profile = source.load_deploy_profile()
        self.feed_path = self.root / self.profile["runtime"]["incoming_feed"]
        self.feed_path.parent.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self.temporary.cleanup()

    def __enter__(self) -> _Fixture:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def record(self, offset: int = 1) -> dict[str, object]:
        day = date(2020, 6, 30) + timedelta(days=offset)
        observed = datetime(day.year, day.month, day.day, 4, tzinfo=timezone.utc)
        stations = self.profile["source_feed"]["station_order_live"]
        return {
            "schema_version": self.profile["source_feed"]["record_schema_version"],
            "date": day.isoformat(),
            "revision_id": f"source-revision-{offset}",
            "observed_at_utc": observed.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "available_at_utc": (observed + timedelta(hours=1)).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
            "finalized_at_utc": (observed + timedelta(hours=2)).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
            "finalized": True,
            "rainfall_mm": float(offset),
            "reservoir_water_level_m": 150.41 + 0.1 * offset,
            "displacement_mm": {
                station: 1000.0 + 10.0 * offset + index
                for index, station in enumerate(stations)
            },
        }

    def payload(
        self,
        count: int = 1,
        *,
        exported_at: str | None = None,
    ) -> dict[str, object]:
        records = [self.record(offset) for offset in range(1, count + 1)]
        if exported_at is None:
            last = datetime(2020, 6, 30, 7, tzinfo=timezone.utc) + timedelta(
                days=count
            )
            exported_at = last.isoformat(timespec="seconds").replace("+00:00", "Z")
        return {
            "schema_version": self.profile["source_feed"]["schema_version"],
            "outcome_source_id": "ootang-machine-source-v1",
            "exported_at_utc": exported_at,
            "records": records,
        }

    def write(self, payload: dict[str, object]) -> None:
        self.feed_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    def ingest(self, *, count: int = 1) -> source.SourceIngestResult:
        self.write(self.payload(count))
        now = datetime(2020, 6, 30, 8, tzinfo=timezone.utc) + timedelta(days=count)
        return source.ingest_source(
            self.profile, runtime_root=self.root, now=now
        )

    @property
    def status_path(self) -> Path:
        return self.root / self.profile["runtime"]["source_status"]

    @property
    def pointer_path(self) -> Path:
        return self.root / self.profile["runtime"]["current_source_pointer"]

    @property
    def activation_path(self) -> Path:
        return self.root / self.profile["runtime"]["activation_source_manifest"]

    @property
    def objects_path(self) -> Path:
        return self.root / self.profile["runtime"]["objects"]


class TestSourceProfile(unittest.TestCase):
    def test_profile_binds_reviewed_live_profile_hash(self) -> None:
        profile = source.load_deploy_profile()
        live_contract = profile["live_profile"]
        self.assertEqual(
            _sha256(ROOT / live_contract["path"]), live_contract["expected_sha256"]
        )

        with tempfile.TemporaryDirectory(prefix="source-profile-", dir=ROOT) as raw:
            root = Path(raw)
            copied = root / "live.json"
            copied.write_bytes((ROOT / live_contract["path"]).read_bytes() + b"\n")
            altered = json.loads(source.DEFAULT_DEPLOY_CONFIG_PATH.read_text())
            altered["live_profile"]["path"] = str(copied)
            path = root / "deploy.json"
            path.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaises(source.SourceConfigError):
                source.load_deploy_profile(path)

    def test_profile_rejects_claiming_an_unverified_runtime_capability(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-profile-", dir=ROOT) as raw:
            payload = json.loads(source.DEFAULT_DEPLOY_CONFIG_PATH.read_text())
            payload["engineering_capabilities"][
                "trusted_anchor_receipt_verified"
            ] = True
            path = Path(raw) / "deploy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(source.SourceConfigError):
                source.load_deploy_profile(path)

    def test_profile_rejects_absolute_and_parent_traversing_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-profile-", dir=ROOT) as raw:
            root = Path(raw)
            path = root / "deploy.json"
            hostile_values = (
                str((root / "absolute-feed.json").resolve()),
                "nested/../../escaped-feed.json",
            )
            for value in hostile_values:
                with self.subTest(value=value):
                    payload = json.loads(
                        source.DEFAULT_DEPLOY_CONFIG_PATH.read_text()
                    )
                    payload["runtime"]["incoming_feed"] = value
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(source.SourceConfigError):
                        source.load_deploy_profile(path)


class TestSourceWaiting(unittest.TestCase):
    def test_missing_feed_waits_without_scientific_artifacts(self) -> None:
        with _Fixture() as fixture:
            result = source.ingest_source(
                fixture.profile,
                runtime_root=fixture.root,
                now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
            )
            self.assertEqual(result.status, "waiting_for_daily_finalized_feed")
            self.assertIsNone(result.source)
            self.assertTrue(fixture.status_path.is_file())
            status = json.loads(fixture.status_path.read_text())
            self.assertEqual(
                status["deploy_profile_sha256"],
                fixture.profile["_profile_sha256"],
            )
            self.assertEqual(
                status["artifact_status"], fixture.profile["artifact_status"]
            )
            self.assertFalse(fixture.pointer_path.exists())
            self.assertFalse(fixture.activation_path.exists())
            self.assertFalse(fixture.objects_path.exists())

    def test_empty_feed_waits_without_scientific_artifacts(self) -> None:
        with _Fixture() as fixture:
            fixture.write(fixture.payload(0, exported_at="2020-07-01T07:00:00Z"))
            result = source.ingest_source(
                fixture.profile,
                runtime_root=fixture.root,
                now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
            )
            self.assertEqual(result.status, "waiting_for_post_baseline_row")
            self.assertFalse(fixture.objects_path.exists())
            self.assertFalse(fixture.pointer_path.exists())

    def test_cli_honors_config_argument_and_writes_waiting_status(self) -> None:
        with _Fixture() as fixture:
            payload = json.loads(source.DEFAULT_DEPLOY_CONFIG_PATH.read_text())
            payload["runtime"]["root"] = str(fixture.root)
            config_path = fixture.root / "deploy-profile.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(source.main(["--config", str(config_path)]), 0)
            status = json.loads(fixture.status_path.read_text())
            self.assertEqual(status["status"], "waiting_for_daily_finalized_feed")

    def test_cli_maps_deploy_lock_contention_to_exit_three(self) -> None:
        with _Fixture() as fixture:
            payload = json.loads(source.DEFAULT_DEPLOY_CONFIG_PATH.read_text())
            payload["runtime"]["root"] = str(fixture.root)
            config_path = fixture.root / "deploy-profile.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            lock_path = fixture.root / fixture.profile["runtime"]["deploy_lock"]
            with lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(source.main(["--config", str(config_path)]), 3)


class TestSourceRuntimeConfinement(unittest.TestCase):
    def test_parent_symlink_escape_is_rejected_before_feed_read(self) -> None:
        with _Fixture() as fixture, tempfile.TemporaryDirectory(
            prefix="ootang-source-outside-", dir=ROOT
        ) as raw_outside:
            outside = Path(raw_outside)
            outside_feed = outside / "feed.json"
            outside_feed.write_text(
                json.dumps(fixture.payload(), separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            (fixture.root / "escape").symlink_to(outside, target_is_directory=True)
            fixture.profile["runtime"]["incoming_feed"] = "escape/feed.json"

            with self.assertRaises(source.SourceConfigError):
                source.ingest_source(
                    fixture.profile,
                    runtime_root=fixture.root,
                    now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                )

            self.assertFalse(fixture.pointer_path.exists())
            self.assertFalse(fixture.objects_path.exists())

    def test_existing_target_symlink_escape_is_rejected_before_status_write(self) -> None:
        with _Fixture() as fixture, tempfile.TemporaryDirectory(
            prefix="ootang-source-outside-", dir=ROOT
        ) as raw_outside:
            outside_status = Path(raw_outside) / "status.json"
            sentinel = b"outside-status-must-not-change\n"
            outside_status.write_bytes(sentinel)
            (fixture.root / "status-link.json").symlink_to(outside_status)
            fixture.profile["runtime"]["source_status"] = "status-link.json"

            with self.assertRaises(source.SourceConfigError):
                source.ingest_source(
                    fixture.profile,
                    runtime_root=fixture.root,
                    now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                )

            self.assertEqual(outside_status.read_bytes(), sentinel)


class TestSourceMaterialization(unittest.TestCase):
    def test_valid_extension_materializes_recursive_content_addressed_source(self) -> None:
        with _Fixture() as fixture:
            result = fixture.ingest(count=2)
            self.assertEqual(result.status, "ready")
            current = result.source
            assert current is not None
            self.assertEqual(current.watermark, date(2020, 7, 2))
            self.assertEqual(len(current.records), 2)
            self.assertEqual(current.records[-1].revision_id, "source-revision-2")
            self.assertEqual(current.frame.iloc[-1]["Date"], "2020-07-02")
            self.assertEqual(
                list(current.frame.columns),
                [
                    "Date",
                    *fixture.profile["model"]["displacement_columns"],
                    "RWL",
                    "RWL_rate",
                    "Rain",
                    "Rain_cum7",
                    "Rain_cum15",
                    "Rain_cum30",
                ],
            )
            for artifact in (current.dataset, current.semantic_manifest):
                self.assertTrue(artifact.path.is_file())
                self.assertTrue(artifact.path.name.startswith(artifact.sha256 + "."))
                self.assertEqual(_sha256(artifact.path), artifact.sha256)
            loaded = source.load_current_source(
                fixture.profile, runtime_root=fixture.root
            )
            pd.testing.assert_frame_equal(loaded.frame, current.frame)

            live_profile = live.load_config()
            live_snapshot = live.load_source_snapshot(
                fixture.activation_path, live_profile
            )
            self.assertEqual(live_snapshot.watermark, date(2020, 7, 2))
            self.assertEqual(
                live_snapshot.data_manifest.sha256,
                current.semantic_manifest.sha256,
            )

    def test_semantically_identical_reexport_preserves_pointer_and_manifest(self) -> None:
        with _Fixture() as fixture:
            first = fixture.ingest()
            assert first.source is not None
            pointer_before = fixture.pointer_path.read_bytes()
            semantic_before = first.source.semantic_manifest.sha256
            payload = fixture.payload(1, exported_at="2020-07-01T07:30:00Z")
            fixture.write(payload)
            second = source.ingest_source(
                fixture.profile,
                runtime_root=fixture.root,
                now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
            )
            assert second.source is not None
            self.assertEqual(fixture.pointer_path.read_bytes(), pointer_before)
            self.assertEqual(second.source.semantic_manifest.sha256, semantic_before)
            status = json.loads(fixture.status_path.read_text())
            self.assertEqual(
                status["reason"],
                "semantically_identical_feed_preserved_existing_pointer",
            )

    def test_feed_advance_never_overwrites_activation_manifest(self) -> None:
        with _Fixture() as fixture:
            first = fixture.ingest()
            assert first.source is not None
            activation_before = fixture.activation_path.read_bytes()
            second = fixture.ingest(count=2)
            assert second.source is not None
            self.assertEqual(second.source.watermark, date(2020, 7, 2))
            self.assertEqual(fixture.activation_path.read_bytes(), activation_before)
            self.assertEqual(
                second.source.activation_manifest.sha256,
                first.source.activation_manifest.sha256,
            )
            self.assertNotEqual(
                second.source.semantic_manifest.sha256,
                first.source.semantic_manifest.sha256,
            )
            activation_source = source.load_activation_source(
                fixture.profile, runtime_root=fixture.root
            )
            self.assertEqual(activation_source.watermark, date(2020, 7, 1))
            self.assertEqual(
                activation_source.dataset.sha256, first.source.dataset.sha256
            )

    def test_same_watermark_revision_updates_current_but_not_activation(self) -> None:
        with _Fixture() as fixture:
            first = fixture.ingest()
            assert first.source is not None
            activation_before = fixture.activation_path.read_bytes()
            payload = fixture.payload()
            payload["records"][0]["revision_id"] = "source-revision-1b"  # type: ignore[index]
            payload["records"][0]["displacement_mm"]["ATU1"] += 2.0  # type: ignore[index]
            payload["exported_at_utc"] = "2020-07-01T07:30:00Z"
            fixture.write(payload)
            second = source.ingest_source(
                fixture.profile,
                runtime_root=fixture.root,
                now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
            )
            assert second.source is not None
            self.assertNotEqual(second.source.dataset.sha256, first.source.dataset.sha256)
            self.assertEqual(fixture.activation_path.read_bytes(), activation_before)

    def test_revision_history_rejects_r1_r2_r1_rollback(self) -> None:
        with _Fixture() as fixture:
            fixture.ingest()
            original = fixture.payload()

            revised = fixture.payload(exported_at="2020-07-01T07:30:00Z")
            revised["records"][0]["revision_id"] = "source-revision-1b"  # type: ignore[index]
            revised["records"][0]["displacement_mm"]["ATU1"] += 2.0  # type: ignore[index]
            fixture.write(revised)
            accepted = source.ingest_source(
                fixture.profile,
                runtime_root=fixture.root,
                now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
            )
            assert accepted.source is not None
            accepted_pointer = fixture.pointer_path.read_bytes()

            original["exported_at_utc"] = "2020-07-01T07:45:00Z"
            fixture.write(original)
            with self.assertRaisesRegex(
                source.SourceIntegrityError, "revision history would roll back"
            ):
                source.ingest_source(
                    fixture.profile,
                    runtime_root=fixture.root,
                    now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                )
            self.assertEqual(fixture.pointer_path.read_bytes(), accepted_pointer)

    def test_saved_old_pointer_is_not_a_valid_snapshot_chain_tip(self) -> None:
        with _Fixture() as fixture:
            fixture.ingest()
            old_pointer = fixture.pointer_path.read_bytes()

            revised = fixture.payload(exported_at="2020-07-01T07:30:00Z")
            revised["records"][0]["revision_id"] = "source-revision-1b"  # type: ignore[index]
            revised["records"][0]["displacement_mm"]["ATU1"] += 2.0  # type: ignore[index]
            fixture.write(revised)
            source.ingest_source(
                fixture.profile,
                runtime_root=fixture.root,
                now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
            )
            latest_pointer = fixture.pointer_path.read_bytes()

            fixture.pointer_path.write_bytes(old_pointer)
            with self.assertRaisesRegex(
                source.SourceIntegrityError, "snapshot chain tip"
            ):
                source.load_current_source(
                    fixture.profile, runtime_root=fixture.root
                )

            # The machine ingest path recovers only a verified predecessor and
            # then preserves the newest accepted semantics.
            fixture.write(revised)
            recovered = source.ingest_source(
                fixture.profile,
                runtime_root=fixture.root,
                now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
            )
            assert recovered.source is not None
            self.assertEqual(fixture.pointer_path.read_bytes(), latest_pointer)
            self.assertEqual(
                recovered.source.records[0].revision_id, "source-revision-1b"
            )

    def test_receipt_first_crash_recovers_revision_before_rejecting_rollback(self) -> None:
        with _Fixture() as fixture:
            fixture.ingest()
            original = fixture.payload(exported_at="2020-07-01T07:45:00Z")
            revised = fixture.payload(exported_at="2020-07-01T07:30:00Z")
            revised["records"][0]["revision_id"] = "source-revision-1b"  # type: ignore[index]
            revised["records"][0]["displacement_mm"]["ATU1"] += 2.0  # type: ignore[index]
            fixture.write(revised)

            atomic_write = source._atomic_write

            def crash_before_public_pointer(path: Path, raw: bytes) -> None:
                if path == fixture.pointer_path:
                    raise OSError("simulated crash before public pointer")
                atomic_write(path, raw)

            with mock.patch.object(
                source, "_atomic_write", side_effect=crash_before_public_pointer
            ):
                with self.assertRaises(source.SourceIntegrityError):
                    source.ingest_source(
                        fixture.profile,
                        runtime_root=fixture.root,
                        now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                    )

            fixture.write(original)
            with self.assertRaisesRegex(
                source.SourceIntegrityError, "revision history would roll back"
            ):
                source.ingest_source(
                    fixture.profile,
                    runtime_root=fixture.root,
                    now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                )
            recovered = source.load_current_source(
                fixture.profile, runtime_root=fixture.root
            )
            self.assertEqual(recovered.records[0].revision_id, "source-revision-1b")

    def test_committed_tip_recovers_before_missing_or_malformed_feed(self) -> None:
        for feed_bytes in (None, b"", b"{not-json"):
            with self.subTest(feed_bytes=feed_bytes):
                with _Fixture() as fixture:
                    fixture.ingest()
                    revised = fixture.payload(
                        exported_at="2020-07-01T07:30:00Z"
                    )
                    revised["records"][0]["revision_id"] = "source-revision-1b"  # type: ignore[index]
                    revised["records"][0]["displacement_mm"]["ATU1"] += 2.0  # type: ignore[index]
                    fixture.write(revised)
                    atomic_write = source._atomic_write

                    def crash_before_pointer(path: Path, raw: bytes) -> None:
                        if path == fixture.pointer_path:
                            raise OSError("simulated crash before public pointer")
                        atomic_write(path, raw)

                    with mock.patch.object(
                        source, "_atomic_write", side_effect=crash_before_pointer
                    ):
                        with self.assertRaises(source.SourceIntegrityError):
                            source.ingest_source(
                                fixture.profile,
                                runtime_root=fixture.root,
                                now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                            )

                    if feed_bytes is None:
                        fixture.feed_path.unlink()
                        result = source.ingest_source(
                            fixture.profile,
                            runtime_root=fixture.root,
                            now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                        )
                        self.assertEqual(
                            result.status, "waiting_for_daily_finalized_feed"
                        )
                    else:
                        fixture.feed_path.write_bytes(feed_bytes)
                        with self.assertRaises(source.SourceInputError):
                            source.ingest_source(
                                fixture.profile,
                                runtime_root=fixture.root,
                                now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                            )
                    recovered = source.load_current_source(
                        fixture.profile, runtime_root=fixture.root
                    )
                    self.assertEqual(
                        recovered.records[0].revision_id, "source-revision-1b"
                    )

    def test_prepared_revision_receipt_can_retry_to_snapshot_commit(self) -> None:
        with _Fixture() as fixture:
            fixture.ingest()
            revised = fixture.payload(exported_at="2020-07-01T07:30:00Z")
            revised["records"][0]["revision_id"] = "source-revision-1b"  # type: ignore[index]
            revised["records"][0]["displacement_mm"]["ATU1"] += 2.0  # type: ignore[index]
            fixture.write(revised)

            with mock.patch.object(
                source,
                "_write_snapshot_receipt",
                side_effect=OSError("simulated crash after prepared revision receipt"),
            ):
                with self.assertRaises(source.SourceIntegrityError):
                    source.ingest_source(
                        fixture.profile,
                        runtime_root=fixture.root,
                        now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                    )

            committed = source.ingest_source(
                fixture.profile,
                runtime_root=fixture.root,
                now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
            )
            assert committed.source is not None
            self.assertEqual(
                committed.source.records[0].revision_id, "source-revision-1b"
            )
            self.assertEqual(committed.source.snapshot_sequence_id, 2)

    def test_in_root_pointer_symlink_cannot_alias_a_content_object(self) -> None:
        with _Fixture() as fixture:
            fixture.ingest()
            pointer_raw = fixture.pointer_path.read_bytes()
            pointer_sha = hashlib.sha256(pointer_raw).hexdigest()
            pointer_object = (
                fixture.root
                / fixture.profile["runtime"]["objects"]
                / f"{pointer_sha}.source-pointer.json"
            )
            object_before = pointer_object.read_bytes()
            fixture.pointer_path.unlink()
            fixture.pointer_path.symlink_to(pointer_object)

            with self.assertRaisesRegex(
                source.SourceConfigError, "must not traverse a symlink"
            ):
                source.ingest_source(
                    fixture.profile,
                    runtime_root=fixture.root,
                    now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                )
            self.assertEqual(pointer_object.read_bytes(), object_before)
            self.assertEqual(_sha256(pointer_object), pointer_sha)

    def test_object_tamper_is_detected_recursively(self) -> None:
        with _Fixture() as fixture:
            result = fixture.ingest()
            assert result.source is not None
            result.source.dataset.path.chmod(0o644)
            result.source.dataset.path.write_bytes(b"{}\n")
            with self.assertRaises(source.SourceIntegrityError):
                source.load_current_source(fixture.profile, runtime_root=fixture.root)

    def test_pointer_cannot_alias_the_immutable_activation(self) -> None:
        with _Fixture() as fixture:
            fixture.ingest()
            forged = fixture.root / "forged-activation.json"
            forged.write_bytes(fixture.activation_path.read_bytes())
            pointer = json.loads(fixture.pointer_path.read_text())
            pointer["activation_source_manifest"] = {
                "path": str(forged.resolve()),
                "sha256": _sha256(forged),
                "size_bytes": forged.stat().st_size,
            }
            fixture.pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

            with self.assertRaises(source.SourceIntegrityError):
                source.load_current_source(
                    fixture.profile, runtime_root=fixture.root
                )
            with self.assertRaises(source.SourceIntegrityError):
                fixture.ingest()

    def test_pointer_semantic_manifest_must_use_its_sha_named_object(self) -> None:
        with _Fixture() as fixture:
            result = fixture.ingest()
            assert result.source is not None
            forged = fixture.root / "semantic-manifest-alias.json"
            forged.write_bytes(result.source.semantic_manifest.path.read_bytes())
            pointer = json.loads(fixture.pointer_path.read_text())
            pointer["semantic_manifest"] = {
                "path": str(forged.resolve()),
                "sha256": _sha256(forged),
                "size_bytes": forged.stat().st_size,
            }
            fixture.pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

            with self.assertRaises(source.SourceIntegrityError):
                source.load_current_source(
                    fixture.profile, runtime_root=fixture.root
                )

    def test_deploy_cycle_lock_is_nonblocking(self) -> None:
        with _Fixture() as fixture:
            lock_path = fixture.root / fixture.profile["runtime"]["deploy_lock"]
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(source.SourceBusyError):
                    source.ingest_source(
                        fixture.profile,
                        runtime_root=fixture.root,
                        now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                    )


class TestFeedRejections(unittest.TestCase):
    def _assert_blocked(
        self,
        mutate: Callable[[dict[str, object]], None],
        *,
        count: int = 1,
        now: datetime | None = None,
    ) -> None:
        with _Fixture() as fixture:
            payload = fixture.payload(count)
            mutate(payload)
            fixture.write(payload)
            with self.assertRaises(source.SourceError):
                source.ingest_source(
                    fixture.profile,
                    runtime_root=fixture.root,
                    now=now
                    or datetime(2020, 6, 30, 8, tzinfo=timezone.utc)
                    + timedelta(days=count),
                )
            status = json.loads(fixture.status_path.read_text())
            self.assertEqual(status["status"], "blocked_integrity")

    def test_unknown_or_external_derived_field_is_rejected(self) -> None:
        def mutate(payload: dict[str, object]) -> None:
            payload["records"][0]["Rain_cum7"] = 99.0  # type: ignore[index]

        self._assert_blocked(mutate)

    def test_station_set_and_order_are_exact(self) -> None:
        def missing(payload: dict[str, object]) -> None:
            del payload["records"][0]["displacement_mm"]["ATU1"]  # type: ignore[index]

        self._assert_blocked(missing)

        def reordered(payload: dict[str, object]) -> None:
            displacement = payload["records"][0]["displacement_mm"]  # type: ignore[index]
            payload["records"][0]["displacement_mm"] = dict(  # type: ignore[index]
                reversed(list(displacement.items()))
            )

        self._assert_blocked(reordered)

    def test_nonfinite_and_negative_rain_are_rejected(self) -> None:
        def negative(payload: dict[str, object]) -> None:
            payload["records"][0]["rainfall_mm"] = -0.1  # type: ignore[index]

        self._assert_blocked(negative)

        with _Fixture() as fixture:
            raw = json.dumps(fixture.payload(), separators=(",", ":"))
            raw = raw.replace('"rainfall_mm":1.0', '"rainfall_mm":NaN')
            fixture.feed_path.write_text(raw, encoding="utf-8")
            with self.assertRaises(source.SourceInputError):
                source.ingest_source(
                    fixture.profile,
                    runtime_root=fixture.root,
                    now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                )

    def test_duplicate_json_key_is_rejected(self) -> None:
        with _Fixture() as fixture:
            payload = json.dumps(fixture.payload(), separators=(",", ":"))
            raw = payload.replace(
                '"outcome_source_id":"ootang-machine-source-v1"',
                '"outcome_source_id":"one","outcome_source_id":"two"',
            )
            fixture.feed_path.write_text(raw, encoding="utf-8")
            with self.assertRaises(source.SourceInputError):
                source.ingest_source(
                    fixture.profile,
                    runtime_root=fixture.root,
                    now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                )

    def test_gap_or_duplicate_dates_are_rejected(self) -> None:
        def gap(payload: dict[str, object]) -> None:
            payload["records"][1]["date"] = "2020-07-03"  # type: ignore[index]

        self._assert_blocked(gap, count=2)

        def duplicate(payload: dict[str, object]) -> None:
            payload["records"][1]["date"] = "2020-07-01"  # type: ignore[index]

        self._assert_blocked(duplicate, count=2)

    def test_timestamp_order_boundary_export_and_machine_now_are_enforced(self) -> None:
        def reversed_time(payload: dict[str, object]) -> None:
            payload["records"][0]["available_at_utc"] = "2020-07-01T03:00:00Z"  # type: ignore[index]

        self._assert_blocked(reversed_time)

        def late_finalize(payload: dict[str, object]) -> None:
            payload["records"][0]["finalized_at_utc"] = "2020-07-01T16:00:00Z"  # type: ignore[index]
            payload["exported_at_utc"] = "2020-07-01T17:00:00Z"

        self._assert_blocked(
            late_finalize, now=datetime(2020, 7, 1, 18, tzinfo=timezone.utc)
        )

        def future_export(payload: dict[str, object]) -> None:
            payload["exported_at_utc"] = "2020-07-01T09:00:00Z"

        self._assert_blocked(
            future_export, now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc)
        )

    def test_regressing_watermark_is_blocked(self) -> None:
        with _Fixture() as fixture:
            fixture.ingest(count=2)
            fixture.write(fixture.payload(count=1))
            with self.assertRaises(source.SourceIntegrityError):
                source.ingest_source(
                    fixture.profile,
                    runtime_root=fixture.root,
                    now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                )

    def test_unexpected_io_error_replaces_stale_ready_status_with_blocked(self) -> None:
        with _Fixture() as fixture:
            fixture.ingest()
            self.assertEqual(
                json.loads(fixture.status_path.read_text())["status"], "ready"
            )

            with mock.patch.object(
                source, "_sha256_file", side_effect=OSError("simulated hash race")
            ):
                with self.assertRaises(source.SourceIntegrityError) as raised:
                    source.ingest_source(
                        fixture.profile,
                        runtime_root=fixture.root,
                        now=datetime(2020, 7, 1, 8, tzinfo=timezone.utc),
                    )

            self.assertIsInstance(raised.exception.__cause__, OSError)
            status = json.loads(fixture.status_path.read_text())
            self.assertEqual(status["status"], "blocked_integrity")
            self.assertIn("OSError:simulated hash race", status["reason"])


class TestHistoricalFeatureEquivalence(unittest.TestCase):
    def test_trusted_derivation_matches_published_features_on_overlap(self) -> None:
        profile = source.load_deploy_profile()
        historical = pd.read_csv(ROOT / profile["historical_base"]["path"])
        raw = pd.DataFrame({"Date": historical["Date"]})
        for station in profile["source_feed"]["station_order_model"]:
            raw[f"{station}_disp"] = historical[f"{station}/mm"]
        raw["RWL"] = historical["RWL/m"]
        raw["Rain"] = historical["Rainfall/mm"]
        derived = source.derive_model_frame(raw, profile)

        expected = pd.read_csv(ROOT / "data" / "features.csv")
        expected = expected.loc[:, derived.columns]
        pd.testing.assert_frame_equal(
            derived,
            expected,
            check_exact=False,
            rtol=1e-12,
            atol=1e-12,
        )


if __name__ == "__main__":
    unittest.main()
