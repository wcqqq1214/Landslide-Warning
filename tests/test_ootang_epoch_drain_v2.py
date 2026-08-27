"""Fast synthetic contracts for the additive R2b-2b-1 blocker observation."""

from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime, timezone
import hashlib
import inspect
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_drain_v2 as drain_v2  # noqa: E402


FIXED_NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)


class DrainV2SyntheticTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="ootang-drain-v2-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self._use_namespace("default")

    def _use_namespace(self, name: str) -> None:
        self.registry_root = self.base / name / "registry"
        self.active_root = self.base / name / "active"
        self.shadow_root = self.base / name / "shadow"

    def _paths(self) -> drain_v2.DrainV2Paths:
        return drain_v2.drain_v2_paths(
            drain_v2.load_drain_v2_profile(),
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )

    def _coordinate(
        self,
        inspection: drain_v2.WorksetInspection,
    ) -> drain_v2.DrainV2Result:
        return drain_v2._coordinate_epoch_drain_v2(  # noqa: SLF001
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
            clock=lambda: FIXED_NOW,
            inspect_workset=lambda _paths, _now: inspection,
        )

    @staticmethod
    def _context(**changes: object) -> drain_v2.WorksetContext:
        values: dict[str, object] = {
            "registry_event_sequence_id": 7,
            "registry_event_entry_sha256": "1" * 64,
            "preparation_event_sequence_id": 3,
            "preparation_event_entry_sha256": "2" * 64,
            "candidate_id": "candidate-a",
            "slot_id": "slot-a",
            "old_live_epoch_id": "old-epoch-a",
            "live_event_count": 11,
            "live_terminal_sha256": "3" * 64,
        }
        values.update(changes)
        return drain_v2.WorksetContext(**values)  # type: ignore[arg-type]

    @classmethod
    def _dirty(
        cls,
        *items: drain_v2.WorksetItem,
        context: drain_v2.WorksetContext | None = None,
    ) -> drain_v2.WorksetInspection:
        return drain_v2.WorksetInspection(
            "dirty_workset_observed",
            "synthetic pending old work",
            tuple(items),
            context or cls._context(),
        )

    @staticmethod
    def _item(
        family: str = "guard", natural_key: str = "guard:2030-01-01"
    ) -> drain_v2.WorksetItem:
        return drain_v2.WorksetItem(
            family, natural_key, "waiting_for_pending_guard", "existing intent"
        )

    @staticmethod
    def _tree_snapshot(root: Path) -> dict[str, str]:
        if not root.exists():
            return {}
        return {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    def test_profile_is_additive_frozen_and_non_authoritative(self) -> None:
        profile = drain_v2.load_drain_v2_profile()

        self.assertEqual(profile["schema_version"], "ootang_epoch_drain_profile_v2")
        self.assertEqual(profile["runtime"]["namespace"], "drain_v2")
        self.assertEqual(profile["runtime"]["observations"], "observations/sha256")
        self.assertEqual(
            profile["protocol"]["observation_schema_version"],
            "ootang_epoch_drain_first_blocker_observation_v2",
        )
        self.assertEqual(
            profile["protocol"]["event_schema_version"],
            "ootang_epoch_drain_first_blocker_observed_event_v2",
        )
        self.assertEqual(
            tuple(profile["protocol"]["workset_families"]), drain_v2.FAMILIES
        )
        expected_hashes = {
            "drain_v1_profile": drain.DEFAULT_CONFIG_SHA256,
            "drain_v1_implementation": (
                "c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602"
            ),
            "eligibility_v1_profile": (
                "2dbda86a747ef486bc06d5c4901e3356404c0079ec2b8aecf91eb61afc91413a"
            ),
            "eligibility_v1_implementation": (
                "46b036aa5e530d0dce50e87b6e4988d67eae1ba4ca67a2d4a09e990f4eaa29bc"
            ),
        }
        self.assertEqual(
            {
                name: binding["expected_sha256"]
                for name, binding in profile["upstream"].items()
            },
            expected_hashes,
        )
        capabilities = profile["engineering_capabilities"]
        self.assertTrue(capabilities["first_blocker_observation_implemented"])
        self.assertTrue(capabilities["observation_binds_r1_r2a_authority"])
        self.assertTrue(capabilities["observation_binds_old_epoch_context"])
        for claim in drain_v2.FALSE_CLAIMS:
            self.assertFalse(capabilities[claim], claim)
        with mock.patch.object(drain_v2, "DEFAULT_CONFIG_SHA256", "0" * 64):
            with self.assertRaises(drain_v2.DrainV2ConfigError):
                drain_v2.load_drain_v2_profile()

    def test_single_blocker_limit_and_repoll_are_deterministic(self) -> None:
        profile = drain_v2.load_drain_v2_profile()
        item = self._item("guard", "guard:b")
        inspection = self._dirty(item)

        first = self._coordinate(inspection)
        paths = self._paths()
        before = {
            "observation": first.observation_path.read_bytes(),
            "event": first.event_path.read_bytes(),
            "head": paths.head.read_bytes(),
        }
        second = self._coordinate(inspection)

        self.assertEqual(first.status, "first_blocker_observed")
        self.assertEqual(second.status, "first_blocker_observation_idempotent")
        self.assertEqual(first.event_path, second.event_path)
        self.assertEqual(first.observation_path, second.observation_path)
        self.assertEqual(first.observation_path.read_bytes(), before["observation"])
        self.assertEqual(first.event_path.read_bytes(), before["event"])
        self.assertEqual(paths.head.read_bytes(), before["head"])
        observation = json.loads(first.observation_path.read_bytes())
        self.assertEqual(
            [(item["family"], item["natural_key"]) for item in observation["items"]],
            [(item.family, item.natural_key)],
        )

        duplicate = self._dirty(item, item)
        with self.assertRaises(drain_v2.DrainV2IntegrityError):
            drain_v2._normalize_inspection(profile, duplicate)  # noqa: SLF001
        self.assertEqual(profile["protocol"]["maximum_items"], 1)

    def test_same_blocker_context_changes_identity_and_is_stale_in_one_namespace(
        self,
    ) -> None:
        item = self._item()
        contexts = (
            self._context(),
            self._context(candidate_id="candidate-b"),
            self._context(old_live_epoch_id="old-epoch-b"),
            self._context(live_terminal_sha256="4" * 64),
        )
        identities: set[tuple[str, bytes]] = set()
        for index, context in enumerate(contexts):
            self._use_namespace(f"context-{index}")
            result = self._coordinate(self._dirty(item, context=context))
            identities.add(
                (result.observation_path.name, result.observation_path.read_bytes())
            )
            self.assertEqual(result.status, "first_blocker_observed")
        self.assertEqual(len(identities), len(contexts))

        self._use_namespace("stale-context")
        first = self._coordinate(self._dirty(item, context=contexts[0]))
        changed = self._coordinate(self._dirty(item, context=contexts[1]))
        self.assertEqual(changed.status, "first_blocker_observation_stale")
        self.assertEqual(changed.observation_path, first.observation_path)
        self.assertEqual(len(tuple(self._paths().observations.iterdir())), 1)

    def test_clean_is_not_applicable_and_dirty_only_observes(self) -> None:
        clean = drain_v2.WorksetInspection(
            "not_applicable_clean_use_v1", "old runtime is synthetically clean"
        )
        clean_result = self._coordinate(clean)
        paths = self._paths()

        self.assertEqual(clean_result.status, "not_applicable_clean_use_v1")
        self.assertIsNone(clean_result.event_path)
        self.assertFalse(paths.events.exists())
        self.assertFalse(paths.observations.exists())
        self.assertEqual(json.loads(paths.status.read_bytes())["event_count"], 0)

        self._use_namespace("dirty")
        with mock.patch.object(drain, "start_epoch_drain") as recovery_producer:
            result = self._coordinate(self._dirty(self._item()))
        recovery_producer.assert_not_called()
        self.assertEqual(result.status, "first_blocker_observed")
        self.assertTrue(result.event_path.is_file())
        self.assertTrue(result.observation_path.is_file())
        self.assertFalse(result.bounded_workset_reservation_implemented)
        self.assertFalse(result.bounded_workset_recovery_implemented)

    def test_v1_authority_supersedes_inert_observation_without_mutation(self) -> None:
        first = self._coordinate(self._dirty(self._item()))
        paths = self._paths()
        v1_event = self.registry_root / "drain_events" / "synthetic.json"
        v1_event.parent.mkdir(parents=True)
        v1_event.write_bytes(b'{"v1":"opaque-authority"}\n')
        before = self._tree_snapshot(v1_event.parent)
        v2_event_before = first.event_path.read_bytes()
        observation_before = first.observation_path.read_bytes()
        inspector = mock.Mock(side_effect=AssertionError("v1 must gate inspection"))

        result = drain_v2._coordinate_epoch_drain_v2(  # noqa: SLF001
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
            clock=lambda: FIXED_NOW,
            inspect_workset=inspector,
        )

        self.assertEqual(result.status, "v1_authority_supersedes_observation")
        inspector.assert_not_called()
        self.assertEqual(self._tree_snapshot(v1_event.parent), before)
        self.assertEqual(first.event_path.read_bytes(), v2_event_before)
        self.assertEqual(first.observation_path.read_bytes(), observation_before)
        status = json.loads(paths.status.read_bytes())
        self.assertFalse(status["lifecycle_authority"])
        self.assertFalse(status["transition_authority"])

        v1_event.unlink()
        (v1_event.parent / "unknown-branch").mkdir()
        with self.assertRaises(drain_v2.DrainV2IntegrityError):
            self._coordinate(self._dirty(self._item()))

    def test_observation_reference_is_exact_and_dereferenced_before_v1_gate(
        self,
    ) -> None:
        profile = drain_v2.load_drain_v2_profile()
        first = self._coordinate(self._dirty(self._item()))
        event = json.loads(first.event_path.read_bytes())
        extra = {**event["observation"], "extra": False}
        wrong_size = {
            **event["observation"],
            "size_bytes": event["observation"]["size_bytes"] + 1,
        }
        for reference in (extra, wrong_size):
            with self.assertRaises(drain_v2.DrainV2IntegrityError):
                drain_v2._load_observation_reference(  # noqa: SLF001
                    profile, self._paths(), reference
                )

        observation = json.loads(first.observation_path.read_bytes())
        observation["item_count"] = True
        malformed_raw = drain_v2._canonical_bytes(observation)  # noqa: SLF001
        malformed_sha = hashlib.sha256(malformed_raw).hexdigest()
        malformed_path = self._paths().observations / f"{malformed_sha}.json"
        malformed_path.write_bytes(malformed_raw)
        with self.assertRaises(drain_v2.DrainV2IntegrityError):
            drain_v2._load_observation_reference(  # noqa: SLF001
                profile,
                self._paths(),
                {
                    "path": malformed_path.name,
                    "sha256": malformed_sha,
                    "size_bytes": len(malformed_raw),
                },
            )

        first.observation_path.unlink()
        v1_event = self.registry_root / "drain_events" / "synthetic.json"
        v1_event.parent.mkdir(parents=True)
        v1_event.write_bytes(b'{"v1":"must-not-mask-missing-object"}\n')
        with self.assertRaises(drain_v2.DrainV2IntegrityError):
            self._coordinate(self._dirty(self._item()))

        self._use_namespace("tampered-object")
        tampered = self._coordinate(self._dirty(self._item()))
        tampered.observation_path.write_bytes(
            tampered.observation_path.read_bytes() + b" "
        )
        with self.assertRaises(drain_v2.DrainV2IntegrityError):
            self._coordinate(self._dirty(self._item()))

    def test_unknown_tamper_and_mutable_caches_do_not_supply_antirollback(
        self,
    ) -> None:
        profile = drain_v2.load_drain_v2_profile()
        unknown = self._dirty(self._item("unknown", "unknown:a"))
        ambiguous = drain_v2.WorksetInspection(
            "waiting_for_ambiguous_family",
            "ambiguous",
            (self._item(),),
            self._context(),
        )
        for inspection in (unknown, ambiguous):
            with self.assertRaises(drain_v2.DrainV2IntegrityError):
                drain_v2._normalize_inspection(profile, inspection)  # noqa: SLF001
        invalid_text = self._dirty(
            drain_v2.WorksetItem(  # type: ignore[arg-type]
                "guard", 7, "waiting_for_pending_guard", "existing intent"
            )
        )
        with self.assertRaises(drain_v2.DrainV2IntegrityError):
            drain_v2._normalize_inspection(profile, invalid_text)  # noqa: SLF001
        with mock.patch.object(
            drain,
            "load_drain_profile",
            side_effect=drain.EpochDrainIntegrityError("synthetic upstream failure"),
        ):
            with self.assertRaises(drain_v2.DrainV2IntegrityError):
                drain_v2._default_inspection(self._paths(), FIXED_NOW)  # noqa: SLF001

        inspection = self._dirty(self._item())
        result = self._coordinate(inspection)
        result.event_path.write_bytes(result.event_path.read_bytes() + b" ")
        with self.assertRaises(drain_v2.DrainV2IntegrityError):
            self._coordinate(inspection)

        self._use_namespace("cache-only")
        result = self._coordinate(inspection)
        paths = self._paths()
        paths.head.write_bytes(b'{"sequence_id":999,"hostile":true}\n')
        paths.status.write_bytes(b'{"event_count":999,"hostile":true}\n')
        repaired = self._coordinate(inspection)
        self.assertEqual(repaired.status, "first_blocker_observation_idempotent")
        self.assertEqual(json.loads(paths.head.read_bytes())["sequence_id"], 1)
        self.assertEqual(json.loads(paths.status.read_bytes())["event_count"], 1)

        # Cache witnesses deliberately do not provide event-suffix anti-rollback.
        result.event_path.unlink()
        replayed = self._coordinate(inspection)
        self.assertEqual(replayed.status, "first_blocker_observed")
        self.assertFalse(
            json.loads(paths.status.read_bytes())["anti_rollback_authority_implemented"]
        )

    def test_all_claims_remain_false_in_every_durable_document(self) -> None:
        result = self._coordinate(self._dirty(self._item()))
        paths = self._paths()
        profile = drain_v2.load_drain_v2_profile()
        documents = (
            profile["engineering_capabilities"],
            json.loads(result.observation_path.read_bytes()),
            json.loads(result.event_path.read_bytes()),
            json.loads(paths.status.read_bytes()),
        )
        for document in documents:
            for claim in drain_v2.FALSE_CLAIMS:
                self.assertIn(claim, document)
                self.assertFalse(document[claim], claim)
        self.assertFalse(json.loads(paths.head.read_bytes())["cache_authority"])
        self.assertFalse(documents[-1]["cache_authority"])
        self.assertFalse(result.lifecycle_authority)
        self.assertFalse(result.transition_authority)

    def test_busy_locks_preserve_caches_and_cli_has_no_human_controls(self) -> None:
        for index, lock_name in enumerate(("manager_lock", "deploy_lock")):
            self._use_namespace(f"busy-{index}")
            paths = self._paths()
            paths.root.mkdir(parents=True)
            sentinels = {
                paths.head: b"hostile-head-sentinel\n",
                paths.status: b"hostile-status-sentinel\n",
            }
            for path, raw in sentinels.items():
                path.write_bytes(raw)
            handle = drain._acquire_lock(  # noqa: SLF001
                getattr(paths, lock_name), label=lock_name
            )
            try:
                with self.assertRaises(drain_v2.DrainV2BusyError):
                    self._coordinate(self._dirty(self._item()))
            finally:
                drain._release_locks([handle])  # noqa: SLF001
            for path, raw in sentinels.items():
                self.assertEqual(path.read_bytes(), raw)
            self.assertFalse(paths.events.exists())
            self.assertFalse(paths.observations.exists())

        signature = inspect.signature(drain_v2.coordinate_epoch_drain_v2)
        self.assertEqual(list(signature.parameters), ["config_path"])
        for forbidden in (
            "--date",
            "--freeze",
            "--approval",
            "--cleanup",
            "--force",
            "--backdate",
        ):
            with self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    drain_v2._parse_args([forbidden])  # noqa: SLF001
        parsed = drain_v2._parse_args(  # noqa: SLF001
            ["--config", str(drain_v2.DEFAULT_CONFIG_PATH)]
        )
        self.assertEqual(parsed.config, drain_v2.DEFAULT_CONFIG_PATH)


if __name__ == "__main__":
    unittest.main()
