"""Contracts for the machine-only bounded Ootang E2-B2 cycle."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_cycle as cycle  # noqa: E402
from monitoring import ootang_live_ledger as ledger_module  # noqa: E402


NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


class _Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ootang-cycle-test-", dir=ROOT
        )
        self.root = Path(self.temporary.name)
        self.profile = cycle.load_cycle_profile()
        self.paths = cycle.runtime_paths(self.profile, runtime_root=self.root)
        self.previous_override = os.environ.get(cycle.TEST_OVERRIDE_ENV)
        os.environ[cycle.TEST_OVERRIDE_ENV] = "1"

    def close(self) -> None:
        if self.previous_override is None:
            os.environ.pop(cycle.TEST_OVERRIDE_ENV, None)
        else:
            os.environ[cycle.TEST_OVERRIDE_ENV] = self.previous_override
        self.temporary.cleanup()

    def __enter__(self) -> _Fixture:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def waiting_dependencies(calls: list[str] | None = None) -> cycle.CycleDependencies:
        observed = calls if calls is not None else []

        def stage(name: str, status: str):
            def invoke() -> cycle.StageOutcome:
                observed.append(name)
                return cycle.StageOutcome(status)

            return invoke

        live_calls = 0

        def live() -> cycle.StageOutcome:
            nonlocal live_calls
            names = (
                "live_reconcile_before_outcome",
                "live_reconcile_after_outcome",
                "live_seal_issue",
            )
            observed.append(names[live_calls % 3])
            live_calls += 1
            return cycle.StageOutcome("waiting_for_production_bundle_or_source_snapshot")

        return cycle.CycleDependencies(
            source_ingest=stage("source_ingest", "waiting_for_daily_finalized_feed"),
            bundle_ensure=stage(
                "bundle_ensure", "waiting_for_semantically_validated_source"
            ),
            live_reconcile=live,
            outcome_materialize=stage(
                "outcome_materialize", "waiting_for_source_model_or_ledger"
            ),
            issue_produce=stage(
                "issue_produce", "waiting_for_source_or_model"
            ),
        )


class CycleProfileTests(unittest.TestCase):
    def test_profile_binds_exact_deploy_and_live_bytes(self) -> None:
        profile = cycle.load_cycle_profile()
        project_root = Path(profile["_project_root"])
        for key in ("deploy_profile", "live_profile"):
            contract = profile[key]
            artifact = project_root / contract["path"]
            self.assertEqual(
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
                contract["expected_sha256"],
            )
        self.assertEqual(
            tuple(profile["cycle"]["stage_order"]), cycle.EXPECTED_STAGE_ORDER
        )

    def test_any_profile_byte_change_is_a_new_version(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cycle-profile-", dir=ROOT) as raw:
            path = Path(raw) / "changed.json"
            payload = json.loads(cycle.DEFAULT_CONFIG_PATH.read_text())
            payload["cycle"]["max_iterations"] = 65
            _write_json(path, payload)
            with self.assertRaisesRegex(cycle.CycleConfigError, "reviewed v1"):
                cycle.load_cycle_profile(path)

    def test_existing_runtime_symlink_cannot_escape_selected_root(self) -> None:
        with _Fixture() as fixture:
            outside = fixture.root.parent / f"{fixture.root.name}-outside"
            outside.mkdir()
            self.addCleanup(outside.rmdir)
            (fixture.root / "outcome_inbox").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                cycle.CycleConfigError, "symbolic-link component"
            ):
                cycle.runtime_paths(fixture.profile, runtime_root=fixture.root)

    def test_mutable_runtime_path_cannot_alias_a_content_object(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="cycle-symlink-alias-", dir=ROOT
        ) as raw:
            runtime_root = Path(raw)
            content_object = (
                runtime_root / "objects" / "sha256" / f"{'a' * 64}.json"
            )
            content_object.parent.mkdir(parents=True)
            original = b'{"immutable":true}\n'
            content_object.write_bytes(original)
            (runtime_root / "cycle_status.json").symlink_to(content_object)
            profile = cycle.load_cycle_profile()

            with self.assertRaisesRegex(
                cycle.CycleConfigError, "symbolic-link component"
            ):
                cycle.runtime_paths(profile, runtime_root=runtime_root)
            self.assertEqual(content_object.read_bytes(), original)


class CycleFixedPointTests(unittest.TestCase):
    def test_empty_input_converges_waiting_in_one_ordered_iteration(self) -> None:
        with _Fixture() as fixture:
            calls: list[str] = []
            token = "a" * 64
            result = cycle.run_cycle(
                runtime_root=fixture.root,
                clock=lambda: NOW,
                dependencies=fixture.waiting_dependencies(calls),
                progress_token_builder=lambda: token,
            )

            self.assertEqual(result.status, "converged_waiting")
            self.assertEqual(result.iterations, 1)
            self.assertEqual(calls, list(cycle.EXPECTED_STAGE_ORDER))
            status = json.loads(result.status_path.read_text())
            self.assertEqual(status["iterations"], 1)
            self.assertEqual(status["progress_token_sha256"], token)
            self.assertFalse(status["e2_live_evidence_eligible"])
            self.assertFalse(status["real_activation_ready"])

    def test_machine_backfill_issue_and_seal_reach_a_bounded_fixed_point(self) -> None:
        with _Fixture() as fixture:
            state = {"phase": 0, "iteration": 0, "live_in_iteration": 0}
            calls: list[tuple[int, str]] = []

            def source() -> cycle.StageOutcome:
                state["iteration"] += 1
                state["live_in_iteration"] = 0
                calls.append((state["iteration"], "source_ingest"))
                return cycle.StageOutcome("ready")

            def bundle() -> cycle.StageOutcome:
                calls.append((state["iteration"], "bundle_ensure"))
                return cycle.StageOutcome("ready")

            def live() -> cycle.StageOutcome:
                live_names = (
                    "live_reconcile_before_outcome",
                    "live_reconcile_after_outcome",
                    "live_seal_issue",
                )
                index = state["live_in_iteration"]
                name = live_names[index]
                state["live_in_iteration"] += 1
                calls.append((state["iteration"], name))
                if state["iteration"] >= 3 and index == 2 and state["phase"] == 2:
                    state["phase"] = 3
                return cycle.StageOutcome("waiting_for_new_data")

            def outcome() -> cycle.StageOutcome:
                calls.append((state["iteration"], "outcome_materialize"))
                if state["iteration"] == 1 and state["phase"] == 0:
                    state["phase"] = 1
                    return cycle.StageOutcome("materialized")
                return cycle.StageOutcome("already_materialized_idempotent")

            def issue() -> cycle.StageOutcome:
                calls.append((state["iteration"], "issue_produce"))
                if state["iteration"] >= 2 and state["phase"] == 1:
                    state["phase"] = 2
                    return cycle.StageOutcome("issued")
                return cycle.StageOutcome("already_issued_idempotent")

            dependencies = cycle.CycleDependencies(
                source_ingest=source,
                bundle_ensure=bundle,
                live_reconcile=live,
                outcome_materialize=outcome,
                issue_produce=issue,
            )
            result = cycle.run_cycle(
                runtime_root=fixture.root,
                clock=lambda: NOW,
                dependencies=dependencies,
                progress_token_builder=lambda: _digest(state["phase"]),
            )

            self.assertEqual(state["phase"], 3)
            self.assertEqual(result.status, "converged_waiting")
            self.assertEqual(result.iterations, 4)
            for iteration in range(1, 5):
                names = [name for observed, name in calls if observed == iteration]
                self.assertEqual(names, list(cycle.EXPECTED_STAGE_ORDER))

    def test_monotonic_backlog_yields_work_remaining_at_the_bound(self) -> None:
        with _Fixture() as fixture:
            remaining = 65
            waiting = fixture.waiting_dependencies()

            def consume_one() -> cycle.StageOutcome:
                nonlocal remaining
                if remaining:
                    remaining -= 1
                return cycle.StageOutcome("ready")

            dependencies = cycle.CycleDependencies(
                source_ingest=consume_one,
                bundle_ensure=waiting.bundle_ensure,
                live_reconcile=waiting.live_reconcile,
                outcome_materialize=waiting.outcome_materialize,
                issue_produce=waiting.issue_produce,
            )
            result = cycle.run_cycle(
                runtime_root=fixture.root,
                clock=lambda: NOW,
                dependencies=dependencies,
                progress_token_builder=lambda: _digest(remaining),
            )
            self.assertEqual(result.status, "work_remaining")
            self.assertEqual(remaining, 1)
            status = json.loads(fixture.paths.cycle_status.read_text())
            self.assertEqual(status["cycle_status"], "work_remaining")
            self.assertEqual(status["iterations"], 64)
            self.assertEqual(len(status["iteration_records"]), 64)

            continued = cycle.run_cycle(
                runtime_root=fixture.root,
                clock=lambda: NOW,
                dependencies=dependencies,
                progress_token_builder=lambda: _digest(remaining),
            )
            self.assertEqual(remaining, 0)
            self.assertEqual(continued.status, "converged_waiting")
            self.assertEqual(continued.iterations, 2)

    def test_repeated_nonadjacent_token_blocks_as_oscillation(self) -> None:
        with _Fixture() as fixture:
            sequence = iter(("a" * 64, "b" * 64, "b" * 64, "a" * 64))
            with self.assertRaisesRegex(cycle.CycleIntegrityError, "oscillated"):
                cycle.run_cycle(
                    runtime_root=fixture.root,
                    clock=lambda: NOW,
                    dependencies=fixture.waiting_dependencies(),
                    progress_token_builder=lambda: next(sequence),
                )
            status = json.loads(fixture.paths.cycle_status.read_text())
            self.assertEqual(status["cycle_status"], "blocked_integrity")

    def test_period_65_oscillation_is_detected_across_scheduler_calls(self) -> None:
        with _Fixture() as fixture:
            state = 0
            waiting = fixture.waiting_dependencies()

            def advance_ring() -> cycle.StageOutcome:
                nonlocal state
                state = (state + 1) % 65
                return cycle.StageOutcome("ready")

            dependencies = cycle.CycleDependencies(
                source_ingest=advance_ring,
                bundle_ensure=waiting.bundle_ensure,
                live_reconcile=waiting.live_reconcile,
                outcome_materialize=waiting.outcome_materialize,
                issue_produce=waiting.issue_produce,
            )
            first = cycle.run_cycle(
                runtime_root=fixture.root,
                clock=lambda: NOW,
                dependencies=dependencies,
                progress_token_builder=lambda: _digest(state),
            )
            self.assertEqual(first.status, "work_remaining")
            self.assertEqual(state, 64)
            first_status = json.loads(first.status_path.read_text())
            self.assertEqual(
                len(first_status["continuation_token_history"]), 65
            )

            with self.assertRaisesRegex(cycle.CycleIntegrityError, "oscillated"):
                cycle.run_cycle(
                    runtime_root=fixture.root,
                    clock=lambda: NOW,
                    dependencies=dependencies,
                    progress_token_builder=lambda: _digest(state),
                )
            self.assertEqual(state, 0)
            blocked = json.loads(fixture.paths.cycle_status.read_text())
            self.assertEqual(blocked["cycle_status"], "blocked_integrity")

    def test_repeated_anchor_failures_do_not_prevent_scientific_convergence(self) -> None:
        with _Fixture() as fixture:
            attempts = 0
            dependencies = fixture.waiting_dependencies()

            def failed_anchor_poll() -> cycle.StageOutcome:
                nonlocal attempts
                attempts += 1
                return cycle.StageOutcome("waiting_for_outcome")

            dependencies = cycle.CycleDependencies(
                source_ingest=dependencies.source_ingest,
                bundle_ensure=dependencies.bundle_ensure,
                live_reconcile=failed_anchor_poll,
                outcome_materialize=dependencies.outcome_materialize,
                issue_produce=dependencies.issue_produce,
            )
            # Three live polls append operational requested/failed attempts, but
            # the injected verified-science digest intentionally remains fixed.
            result = cycle.run_cycle(
                runtime_root=fixture.root,
                clock=lambda: NOW,
                dependencies=dependencies,
                progress_token_builder=lambda: _digest("same-science"),
            )
            self.assertEqual(attempts, 3)
            self.assertEqual(result.iterations, 1)
            self.assertEqual(result.status, "converged_waiting")


class CycleProgressTokenTests(unittest.TestCase):
    def test_pre_genesis_ledgers_are_recoverable_but_damage_is_not(self) -> None:
        for state in ("zero_byte", "verified_empty_schema"):
            with self.subTest(state=state), _Fixture() as fixture:
                fixture.paths.ledger.parent.mkdir(parents=True, exist_ok=True)
                if state == "zero_byte":
                    fixture.paths.ledger.write_bytes(b"")
                    expected = "recoverable_zero_byte_pre_genesis"
                else:
                    ledger_module.AppendOnlyLedger(fixture.paths.ledger)
                    expected = "recoverable_verified_schema_pre_genesis"
                payload = cycle.progress_token_payload(fixture.paths)
                projection = payload["verified_scientific_projection"]
                self.assertEqual(projection["state"], expected)

                calls: list[str] = []
                result = cycle.run_cycle(
                    runtime_root=fixture.root,
                    clock=lambda: NOW,
                    dependencies=fixture.waiting_dependencies(calls),
                )
                self.assertEqual(result.status, "converged_waiting")
                self.assertEqual(calls, list(cycle.EXPECTED_STAGE_ORDER))

        with _Fixture() as fixture:
            fixture.paths.ledger.parent.mkdir(parents=True, exist_ok=True)
            fixture.paths.ledger.write_bytes(b"damaged non-SQLite ledger")
            with self.assertRaises(cycle.CycleIntegrityError):
                cycle.build_progress_token(fixture.paths)

    def test_verified_projection_digest_excludes_raw_ledger_attempt_head(self) -> None:
        base = {
            "epoch_id": "epoch",
            "states": {"S1": object()},
            "last_finalized_date": datetime(2030, 1, 1).date(),
            "latest_displacement_mm": {"S1": 1.0},
            "outstanding_target_date": datetime(2030, 1, 2).date(),
            "outstanding_issue_id": "issue",
            "issue_events": {},
            "seal_event": None,
            "anchored_seal_hashes": {},
            "settled_events": {},
            "backfill_events": {},
            "revision_ids": {},
            "latest_actuals_by_date": {},
            "blind_settled_count": 0,
            "engineering_blind_candidate_count": 0,
            "backfill_count": 0,
        }
        first = SimpleNamespace(
            **base,
            ledger_event_count=10,
            ledger_terminal_sequence_id=10,
            ledger_terminal_sha256="a" * 64,
        )
        second = SimpleNamespace(
            **base,
            ledger_event_count=12,
            ledger_terminal_sequence_id=12,
            ledger_terminal_sha256="b" * 64,
        )
        def hasher(_state: object) -> str:
            return "c" * 64

        self.assertEqual(
            cycle.scientific_projection_payload(
                first, station_state_hasher=hasher
            ),
            cycle.scientific_projection_payload(
                second, station_state_hasher=hasher
            ),
        )

    def test_operational_status_time_and_reason_are_excluded(self) -> None:
        with _Fixture() as fixture:
            first = {
                "schema_version": "ootang_prequential_live_status_v1",
                "runner_status": "waiting_for_new_data",
                "reason": "first poll",
                "recorded_at_utc": "2030-01-01T00:00:00Z",
                "ledger_event_count": 0,
                "ledger_terminal_sha256": "0" * 64,
            }
            _write_json(fixture.paths.live_status, first)
            before = cycle.build_progress_token(fixture.paths)
            first["runner_status"] = "waiting_for_backfill_outcome"
            first["reason"] = "later poll with the same science"
            first["recorded_at_utc"] = "2030-01-02T00:00:00Z"
            _write_json(fixture.paths.live_status, first)
            after_time_noise = cycle.build_progress_token(fixture.paths)
            self.assertEqual(before, after_time_noise)

            first["ledger_event_count"] = 1
            first["ledger_terminal_sha256"] = "1" * 64
            _write_json(fixture.paths.live_status, first)
            self.assertEqual(after_time_noise, cycle.build_progress_token(fixture.paths))

            first["outstanding_target_date"] = "2030-01-02"
            _write_json(fixture.paths.live_status, first)
            self.assertEqual(
                after_time_noise, cycle.build_progress_token(fixture.paths)
            )

            fixture.paths.model_manifest.parent.mkdir(parents=True)
            fixture.paths.model_manifest.write_bytes(b'{"model":"new-science"}\n')
            self.assertNotEqual(
                after_time_noise, cycle.build_progress_token(fixture.paths)
            )

    def test_issue_and_outcome_exact_bytes_are_scientific_progress(self) -> None:
        with _Fixture() as fixture:
            before = cycle.build_progress_token(fixture.paths)
            issue = fixture.paths.issue_inbox / "2030-01-02.json"
            issue.parent.mkdir(parents=True)
            issue.write_bytes(b'{"issue":1}\n')
            with_issue = cycle.build_progress_token(fixture.paths)
            self.assertNotEqual(before, with_issue)
            outcome = fixture.paths.outcome_inbox / "2030-01-02.json"
            outcome.parent.mkdir(parents=True)
            outcome.write_bytes(b'{"outcome":1}\n')
            self.assertNotEqual(with_issue, cycle.build_progress_token(fixture.paths))

    def test_receipt_registry_is_part_of_crash_recovery_progress(self) -> None:
        with _Fixture() as fixture:
            before = cycle.build_progress_token(fixture.paths)
            issue_receipt = fixture.paths.issue_receipts / "2030-01-02.json"
            issue_receipt.parent.mkdir(parents=True)
            issue_receipt.write_bytes(b'{"receipt":"issue"}\n')
            after_issue_receipt = cycle.build_progress_token(fixture.paths)
            self.assertNotEqual(before, after_issue_receipt)
            outcome_receipt = (
                fixture.paths.outcome_receipts
                / "2030-01-02"
                / f"{'a' * 64}.json"
            )
            outcome_receipt.parent.mkdir(parents=True)
            outcome_receipt.write_bytes(b'{"receipt":"outcome"}\n')
            self.assertNotEqual(
                after_issue_receipt, cycle.build_progress_token(fixture.paths)
            )

    def test_inbox_and_receipt_enumeration_reject_symbolic_links(self) -> None:
        for registry_name in ("issue_inbox", "outcome_receipts"):
            with self.subTest(registry=registry_name), _Fixture() as fixture:
                registry = getattr(fixture.paths, registry_name)
                registry.mkdir(parents=True)
                target = fixture.root / f"{registry_name}-target.json"
                target.write_bytes(b"{}\n")
                (registry / "linked.json").symlink_to(target)
                with self.assertRaisesRegex(
                    cycle.CycleIntegrityError, "symbolic link"
                ):
                    cycle.build_progress_token(fixture.paths)


class CycleLockAndFailureTests(unittest.TestCase):
    def test_test_overrides_require_explicit_environment_gate(self) -> None:
        with _Fixture() as fixture:
            overrides = (
                {"dependencies": fixture.waiting_dependencies()},
                {"progress_token_builder": lambda: "a" * 64},
                {"clock": lambda: NOW},
            )
            previous = os.environ.pop(cycle.TEST_OVERRIDE_ENV, None)
            try:
                for override in overrides:
                    with self.subTest(override=next(iter(override))):
                        with self.assertRaisesRegex(
                            cycle.CycleConfigError, "explicit test mode"
                        ):
                            cycle.run_cycle(
                                runtime_root=fixture.root,
                                **override,
                            )
            finally:
                if previous is not None:
                    os.environ[cycle.TEST_OVERRIDE_ENV] = previous
            self.assertFalse(fixture.paths.cycle_status.exists())

    def test_outer_cycle_lock_is_nonblocking(self) -> None:
        with _Fixture() as fixture:
            fixture.paths.cycle_lock.parent.mkdir(parents=True, exist_ok=True)
            with fixture.paths.cycle_lock.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(cycle.CycleBusyError):
                    cycle.run_cycle(
                        runtime_root=fixture.root,
                        clock=lambda: NOW,
                        dependencies=fixture.waiting_dependencies(),
                        progress_token_builder=lambda: "a" * 64,
                    )
            self.assertFalse(fixture.paths.cycle_status.exists())

    def test_stage_calls_are_not_made_while_child_locks_are_preheld(self) -> None:
        with _Fixture() as fixture:
            deploy = fixture.root / fixture.profile["_deploy_profile_payload"][
                "runtime"
            ]["deploy_lock"]
            runner = fixture.root / fixture.profile["_live_profile_payload"][
                "runtime"
            ]["lock"]
            deploy.parent.mkdir(parents=True, exist_ok=True)
            with deploy.open("a+b") as deploy_handle, runner.open("a+b") as runner_handle:
                fcntl.flock(deploy_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(runner_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = cycle.run_cycle(
                    runtime_root=fixture.root,
                    clock=lambda: NOW,
                    dependencies=fixture.waiting_dependencies(),
                    progress_token_builder=lambda: "a" * 64,
                )
            self.assertEqual(result.status, "converged_waiting")

    def test_scientific_snapshot_uses_nonblocking_child_locks(self) -> None:
        with _Fixture() as fixture:
            fixture.paths.deploy_lock.parent.mkdir(parents=True, exist_ok=True)
            with fixture.paths.deploy_lock.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(cycle.CycleBusyError, "deploy lock"):
                    cycle.build_progress_token(fixture.paths)

    def test_unknown_fatal_stage_status_is_blocked(self) -> None:
        with _Fixture() as fixture:
            dependencies = fixture.waiting_dependencies()
            dependencies = cycle.CycleDependencies(
                source_ingest=lambda: cycle.StageOutcome("fatal_integrity"),
                bundle_ensure=dependencies.bundle_ensure,
                live_reconcile=dependencies.live_reconcile,
                outcome_materialize=dependencies.outcome_materialize,
                issue_produce=dependencies.issue_produce,
            )
            with self.assertRaisesRegex(
                cycle.CycleIntegrityError, "unknown or fatal status"
            ):
                cycle.run_cycle(
                    runtime_root=fixture.root,
                    clock=lambda: NOW,
                    dependencies=dependencies,
                    progress_token_builder=lambda: "a" * 64,
                )

    def test_returned_status_path_must_match_and_confirm_result(self) -> None:
        with _Fixture() as fixture:
            hostile = fixture.root / "unbound-source-status.json"
            _write_json(
                hostile,
                {
                    "schema_version": "ootang_source_ingest_status_v1",
                    "status": "ready",
                    "formal_warning_output": False,
                },
            )
            dependencies = fixture.waiting_dependencies()
            dependencies = cycle.CycleDependencies(
                source_ingest=lambda: cycle.StageOutcome("ready", hostile),
                bundle_ensure=dependencies.bundle_ensure,
                live_reconcile=dependencies.live_reconcile,
                outcome_materialize=dependencies.outcome_materialize,
                issue_produce=dependencies.issue_produce,
            )
            with self.assertRaisesRegex(cycle.CycleIntegrityError, "outside its contract"):
                cycle.run_cycle(
                    runtime_root=fixture.root,
                    clock=lambda: NOW,
                    dependencies=dependencies,
                    progress_token_builder=lambda: "a" * 64,
                )

    def test_unexpected_stage_failure_is_normalized_and_fail_closed(self) -> None:
        with _Fixture() as fixture:
            dependencies = fixture.waiting_dependencies()

            def explode() -> object:
                raise OSError("injected I/O failure")

            dependencies = cycle.CycleDependencies(
                source_ingest=dependencies.source_ingest,
                bundle_ensure=explode,
                live_reconcile=dependencies.live_reconcile,
                outcome_materialize=dependencies.outcome_materialize,
                issue_produce=dependencies.issue_produce,
            )
            with self.assertRaisesRegex(
                cycle.CycleIntegrityError, "stage bundle_ensure failed"
            ):
                cycle.run_cycle(
                    runtime_root=fixture.root,
                    clock=lambda: NOW,
                    dependencies=dependencies,
                    progress_token_builder=lambda: "a" * 64,
                )
            status = json.loads(fixture.paths.cycle_status.read_text())
            self.assertEqual(status["cycle_status"], "blocked_integrity")
            self.assertFalse(status["formal_warning_output"])
            self.assertFalse(status["trusted_anchor_receipt_verified"])
            self.assertFalse(status["automatic_epoch_rotation_implemented"])

            def keys(value: object):
                if isinstance(value, dict):
                    for key, child in value.items():
                        yield key.lower()
                        yield from keys(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from keys(child)

            forbidden = ("manual", "human", "operator", "freeze")
            self.assertFalse(
                any(marker in key for key in keys(status) for marker in forbidden)
            )


if __name__ == "__main__":
    unittest.main()
