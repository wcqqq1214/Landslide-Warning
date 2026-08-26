import base64
from dataclasses import replace
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_trusted_time_shadow_core as trusted_time


FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "ootang_trusted_time" / "production_probe.json"
)
FIXED_NOW = datetime(2026, 8, 26, 13, 0, tzinfo=timezone.utc)
FIXED_NONCE = int(
    "a29e1ff21b4ae3a032645fa211d3cf54ceb7085252a60c96f1de4b6a372d8f5f",
    16,
)


class TrustedTimeShadowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = trusted_time.load_trusted_time_profile()
        cls.paths = trusted_time.trusted_time_paths(cls.profile)
        cls.trust = trusted_time.load_trust_material(cls.profile, cls.paths)
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.fixture_message = base64.b64decode(cls.fixture["request"]["message_base64"])
        cls.fixture_request = base64.b64decode(cls.fixture["request"]["tsq_base64"])
        cls.fixture_response = base64.b64decode(cls.fixture["response"]["tsr_base64"])

    def test_profile_dependency_and_pinned_trust_are_exact(self):
        self.assertEqual(
            trusted_time._check_dependency(self.profile),  # noqa: SLF001
            "1.0.8",
        )
        self.assertEqual(
            self.trust.leaf_der_sha256,
            "85f927bc07ab62cac3b44356c10efc81b2c6883fda7ab9e6d870d9d13acd05b7",
        )
        self.assertEqual(
            self.trust.root_der_sha256,
            "2aca8fea5d3ce48b01cc77076293c280e6c23ffe44034757ee7833ca9f45d633",
        )
        with mock.patch.object(trusted_time, "package_version", return_value="1.0.7"):
            with self.assertRaises(trusted_time.TrustedTimeConfigError):
                trusted_time._check_dependency(self.profile)  # noqa: SLF001
        with mock.patch.object(trusted_time.sys, "version_info", (3, 10, 19)):
            with self.assertRaises(trusted_time.TrustedTimeConfigError):
                trusted_time.load_trusted_time_profile()

    def test_coordinated_config_trust_swap_is_rejected_by_core_constants(self):
        profile = json.loads(
            (ROOT / "config" / "ootang_trusted_time_shadow.v1.json").read_text(
                encoding="utf-8"
            )
        )
        profile["trust"]["leaf_pem_sha256"] = "0" * 64
        profile["trust"]["leaf_der_sha256"] = "1" * 64
        profile["trust"]["root_pem_sha256"] = "2" * 64
        profile["trust"]["root_der_sha256"] = "3" * 64
        with tempfile.TemporaryDirectory() as tmp_dir:
            hostile = Path(tmp_dir) / "profile.json"
            hostile.write_bytes(trusted_time._canonical_bytes(profile))  # noqa: SLF001
            with self.assertRaises(trusted_time.TrustedTimeConfigError):
                trusted_time.load_trusted_time_profile(hostile, project_root=ROOT)

    def test_runtime_root_and_shared_lock_namespace_are_immutable(self):
        for key, value in (
            ("root", "runtime/alternate"),
            ("runner_lock", "alternate.lock"),
        ):
            profile = json.loads(
                (ROOT / "config" / "ootang_trusted_time_shadow.v1.json").read_text(
                    encoding="utf-8"
                )
            )
            profile["runtime"][key] = value
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp_dir:
                hostile = Path(tmp_dir) / "profile.json"
                hostile.write_bytes(
                    trusted_time._canonical_bytes(profile)  # noqa: SLF001
                )
                with self.assertRaises(trusted_time.TrustedTimeConfigError):
                    trusted_time.load_trusted_time_profile(hostile, project_root=ROOT)

    def test_custom_der_request_is_exact_real_256_bit_policy_fixture(self):
        request = trusted_time.build_timestamp_request(
            self.fixture_message,
            FIXED_NONCE,
            self.profile["rfc3161"]["policy_oid"],
        )

        self.assertEqual(FIXED_NONCE.bit_length(), 256)
        self.assertEqual(request, self.fixture_request)
        self.assertEqual(len(request), 105)
        self.assertEqual(
            hashlib.sha256(request).hexdigest(),
            "bdc94a42cd34ba1a947c521b19553edea9a7699c621c8ff66ba4458382acbfa3",
        )
        self.assertIn(bytes.fromhex("06092b0601040183bf3002"), request)
        self.assertTrue(request.endswith(b"\x01\x01\xff"))
        with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
            trusted_time.build_timestamp_request(
                self.fixture_message, 2**127, self.profile["rfc3161"]["policy_oid"]
            )

    def test_real_sigstore_response_verifies_fully_offline(self):
        facts = trusted_time.verify_timestamp_response(
            self.fixture_response,
            self.fixture_message,
            FIXED_NONCE,
            date(2026, 8, 27),
            self.profile,
            self.trust,
        )

        self.assertEqual(facts["pki_status"], 0)
        self.assertEqual(facts["policy_oid"], "1.3.6.1.4.1.57264.2")
        self.assertEqual(facts["accuracy_seconds"], 1.0)
        self.assertEqual(facts["target_boundary_utc"], "2026-08-26T16:00:00.000000Z")
        self.assertEqual(facts["signer_count"], 1)
        self.assertTrue(facts["tsa_name_matches_leaf_subject"])
        self.assertTrue(facts["signature_and_chain_verified"])
        self.assertTrue(facts["causality_before_target"])
        self.assertEqual(
            facts["trusted_upper_bound_utc"], "2026-08-26T12:53:47.000000Z"
        )

    def test_real_response_rejects_message_nonce_and_der_tampering(self):
        cases = (
            (self.fixture_response, self.fixture_message + b"x", FIXED_NONCE),
            (self.fixture_response, self.fixture_message, FIXED_NONCE + 1),
            (self.fixture_response[:-1], self.fixture_message, FIXED_NONCE),
        )
        for response, message, nonce in cases:
            with self.subTest(message=message[-1:], nonce=nonce):
                with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
                    trusted_time.verify_timestamp_response(
                        response,
                        message,
                        nonce,
                        date(2026, 8, 27),
                        self.profile,
                        self.trust,
                    )

    def test_real_response_reports_late_causality_without_backdating(self):
        facts = trusted_time.verify_timestamp_response(
            self.fixture_response,
            self.fixture_message,
            FIXED_NONCE,
            date(2026, 8, 26),
            self.profile,
            self.trust,
        )

        self.assertFalse(facts["causality_before_target"])

    def test_trust_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            link = Path(tmp_dir) / "leaf.pem"
            link.symlink_to(self.paths.leaf)
            hostile_paths = replace(self.paths, leaf=link)
            with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
                trusted_time.load_trust_material(self.profile, hostile_paths)

    def test_runtime_namespace_rejects_symlink_components(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            real = root / "real"
            real.mkdir()
            (root / "redirect").symlink_to(real, target_is_directory=True)
            with self.assertRaises(trusted_time.TrustedTimeConfigError):
                trusted_time._runtime_child(  # noqa: SLF001
                    root, "redirect/artifact.json", name="test.runtime"
                )

    def test_empty_runtime_waits_without_network_or_receipt(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            transport = mock.Mock(
                side_effect=AssertionError("empty runtime must not use network")
            )
            status_path = trusted_time.poll_trusted_time_shadow(
                runtime_root=Path(tmp_dir), transport=transport, clock=lambda: FIXED_NOW
            )
            status = json.loads(status_path.read_text(encoding="utf-8"))

        transport.assert_not_called()
        self.assertEqual(status["runner_status"], "waiting_for_live_prerequisites")
        self.assertFalse(status["rfc3161_receipt_verified"])
        self.assertFalse(status["cryptographic_time_shadow_verified"])
        self.assertFalse(status["trusted_anchor_receipt_verified"])
        self.assertFalse(status["e2_live_evidence_eligible"])
        self.assertFalse(status["real_activation_ready"])
        self.assertFalse(status["formal_warning_output"])

    def test_shared_runner_lock_is_nonblocking_busy(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = Path(tmp_dir)
            lock_path = runtime / "runner.lock"
            lock_path.touch()
            with lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(trusted_time.TrustedTimeBusyError):
                    trusted_time.poll_trusted_time_shadow(
                        runtime_root=runtime, clock=lambda: FIXED_NOW
                    )

    def test_runner_lock_rejects_nonregular_and_replaced_inode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = Path(tmp_dir)
            fifo = runtime / "fifo.lock"
            os.mkfifo(fifo)
            with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
                trusted_time._acquire_lock(fifo)  # noqa: SLF001

            lock_path = runtime / "replace.lock"
            lock_path.touch()

            def replace_lock(*_args):
                lock_path.rename(runtime / "original.lock")
                lock_path.touch()

            with (
                mock.patch.object(
                    trusted_time.fcntl, "flock", side_effect=replace_lock
                ),
                self.assertRaises(trusted_time.TrustedTimeIntegrityError),
            ):
                trusted_time._acquire_lock(lock_path)  # noqa: SLF001

    def test_candidate_requires_exact_completion_and_never_reads_outcome(self):
        target = date(2026, 8, 27)
        seal = SimpleNamespace(
            sequence_id=13,
            entry_sha256="a" * 64,
            event_key="issue:2026-08-27:sealed",
            event_type="issue_batch_sealed",
            target_date=target.isoformat(),
        )
        projection = SimpleNamespace(
            outstanding_target_date=target,
            outstanding_issue_id="issue-1",
            seal_event=seal,
            epoch_id="epoch-1",
        )
        guard = {
            "intents": [
                {
                    "target_date": target.isoformat(),
                    "model_manifest_sha256": "b" * 64,
                }
            ],
            "completions": [
                {
                    "target_date": target.isoformat(),
                    "live_issue_seal_event_key": seal.event_key,
                    "live_issue_seal_entry_science_sha256": "c" * 64,
                    "completion_semantic_sha256": "d" * 64,
                    "intent_semantic_sha256": "e" * 64,
                    "replay_semantic_sha256": "f" * 64,
                    "issue_sha256": "1" * 64,
                    "input_manifest_sha256": "2" * 64,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = Path(tmp_dir)
            outcome = runtime / "outcome_inbox"
            outcome.mkdir()
            (outcome / target.isoformat()).write_bytes(b"not json and must be ignored")
            paths = trusted_time.trusted_time_paths(self.profile, runtime_root=runtime)
            with (
                mock.patch.object(trusted_time.live, "load_config", return_value={}),
                mock.patch.object(
                    trusted_time.live, "runtime_paths", return_value=SimpleNamespace()
                ),
                mock.patch.object(
                    trusted_time.live, "load_prerequisites", return_value=object()
                ),
                mock.patch.object(
                    trusted_time.live,
                    "load_verified_ledger_projection",
                    return_value=projection,
                ),
                mock.patch.object(
                    trusted_time.verified_live,
                    "guard_progress_payload",
                    return_value=guard,
                ),
            ):
                candidate, reason = trusted_time._candidate(  # noqa: SLF001
                    self.profile, paths
                )

        self.assertEqual(reason, "ready_for_timestamp_request")
        self.assertEqual(candidate.target, target)
        self.assertFalse(candidate.envelope["outcome_read"])
        self.assertEqual(candidate.envelope["model_manifest_sha256"], "b" * 64)

    @staticmethod
    def _candidate_value(payload="stable"):
        return trusted_time.Candidate(
            date(2026, 8, 27),
            {
                "schema_version": "test-envelope-v1",
                "payload": payload,
                "outcome_read": False,
            },
        )

    @staticmethod
    def _verification_facts():
        return {
            "pki_status": 0,
            "gen_time_utc": "2026-08-26T12:00:00.000000Z",
            "accuracy_seconds": 1.0,
            "trusted_upper_bound_utc": "2026-08-26T12:00:01.000000Z",
            "causality_before_target": True,
            "signature_and_chain_verified": True,
        }

    def test_network_failure_is_retryable_and_preserves_first_request(self):
        candidate = self._candidate_value()
        transport = mock.Mock(
            side_effect=trusted_time.TrustedTimeNetworkError("offline")
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = Path(tmp_dir)
            with mock.patch.object(
                trusted_time, "_candidate", return_value=(candidate, "ready")
            ):
                first = trusted_time.poll_trusted_time_shadow(
                    runtime_root=runtime,
                    transport=transport,
                    clock=lambda: FIXED_NOW,
                    nonce_factory=lambda: FIXED_NONCE,
                )
                request_path = (
                    runtime / "trusted_time_shadow_requests" / "2026-08-27.json"
                )
                first_request = request_path.read_bytes()
                second = trusted_time.poll_trusted_time_shadow(
                    runtime_root=runtime,
                    transport=transport,
                    clock=lambda: FIXED_NOW,
                    nonce_factory=lambda: 2**255,
                )
                second_request = request_path.read_bytes()
            first_status = json.loads(first.read_text(encoding="utf-8"))
            second_status = json.loads(second.read_text(encoding="utf-8"))

        self.assertEqual(first_request, second_request)
        self.assertEqual(first_status["runner_status"], "waiting_for_timestamp_service")
        self.assertEqual(
            second_status["runner_status"], "waiting_for_timestamp_service"
        )
        self.assertFalse(second_status["rfc3161_receipt_verified"])

    def test_semantically_equal_noncanonical_request_is_rejected(self):
        candidate = self._candidate_value()
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = Path(tmp_dir)
            with mock.patch.object(
                trusted_time, "_candidate", return_value=(candidate, "ready")
            ):
                trusted_time.poll_trusted_time_shadow(
                    runtime_root=runtime,
                    transport=mock.Mock(
                        side_effect=trusted_time.TrustedTimeNetworkError("offline")
                    ),
                    clock=lambda: FIXED_NOW,
                    nonce_factory=lambda: FIXED_NONCE,
                )
                request_path = (
                    runtime / "trusted_time_shadow_requests" / "2026-08-27.json"
                )
                payload = json.loads(request_path.read_text(encoding="utf-8"))
                request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
                    trusted_time.poll_trusted_time_shadow(
                        runtime_root=runtime,
                        transport=mock.Mock(),
                        clock=lambda: FIXED_NOW,
                    )

    def test_transient_http_statuses_retry_but_fixed_client_error_blocks(self):
        for status, error_type in (
            (408, trusted_time.TrustedTimeNetworkError),
            (425, trusted_time.TrustedTimeNetworkError),
            (429, trusted_time.TrustedTimeNetworkError),
            (503, trusted_time.TrustedTimeNetworkError),
            (400, trusted_time.TrustedTimeIntegrityError),
        ):
            error = trusted_time.urllib.error.HTTPError(
                self.profile["rfc3161"]["endpoint"], status, "test", {}, None
            )
            opener = mock.Mock()
            opener.open.side_effect = error
            with (
                self.subTest(status=status),
                mock.patch.object(
                    trusted_time.urllib.request,
                    "build_opener",
                    return_value=opener,
                ),
                self.assertRaises(error_type),
            ):
                trusted_time.post_timestamp_query(
                    self.profile["rfc3161"]["endpoint"], b"request", 1.0, 1024
                )

    def test_transport_total_deadline_interrupts_slow_open(self):
        opener = mock.Mock()

        def slow_open(*_args, **_kwargs):
            trusted_time.time_module.sleep(1.0)
            raise AssertionError("deadline failed to interrupt slow transport")

        opener.open.side_effect = slow_open
        started = trusted_time.time_module.monotonic()
        with (
            mock.patch.object(
                trusted_time.urllib.request, "build_opener", return_value=opener
            ),
            self.assertRaises(trusted_time.TrustedTimeNetworkError),
        ):
            trusted_time.post_timestamp_query(
                self.profile["rfc3161"]["endpoint"], b"request", 0.05, 1024
            )
        self.assertLess(trusted_time.time_module.monotonic() - started, 0.5)

    def test_transport_rejects_an_inherited_blocked_deadline_signal(self):
        opener = mock.Mock()
        previous_mask = trusted_time.signal.pthread_sigmask(
            trusted_time.signal.SIG_BLOCK,
            {trusted_time.signal.SIGALRM},
        )
        try:
            with (
                mock.patch.object(
                    trusted_time.urllib.request,
                    "build_opener",
                    return_value=opener,
                ),
                self.assertRaisesRegex(
                    trusted_time.TrustedTimeIntegrityError,
                    "deadline signal is blocked",
                ),
            ):
                trusted_time.post_timestamp_query(
                    self.profile["rfc3161"]["endpoint"],
                    b"request",
                    0.05,
                    1024,
                )
        finally:
            trusted_time.signal.pthread_sigmask(
                trusted_time.signal.SIG_SETMASK,
                previous_mask,
            )
        opener.open.assert_not_called()

    def test_transport_redirect_media_compression_and_size_fail_closed(self):
        candidate = self._candidate_value()
        responses = (
            trusted_time.TransportResponse(
                b"x", 200, "text/plain", None, self.profile["rfc3161"]["endpoint"]
            ),
            trusted_time.TransportResponse(
                b"x",
                200,
                "application/timestamp-reply",
                "gzip",
                self.profile["rfc3161"]["endpoint"],
            ),
            trusted_time.TransportResponse(
                b"x",
                200,
                "application/timestamp-reply",
                None,
                "https://example.invalid/redirect",
            ),
            trusted_time.TransportResponse(
                b"x" * 65537,
                200,
                "application/timestamp-reply",
                None,
                self.profile["rfc3161"]["endpoint"],
            ),
        )
        for index, response in enumerate(responses):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp_dir:
                with mock.patch.object(
                    trusted_time, "_candidate", return_value=(candidate, "ready")
                ):
                    with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
                        trusted_time.poll_trusted_time_shadow(
                            runtime_root=Path(tmp_dir),
                            transport=lambda *_args, response=response: response,
                            clock=lambda: FIXED_NOW,
                            monotonic=iter((1.0, 1.1)).__next__,
                            nonce_factory=lambda: FIXED_NONCE,
                        )

    def test_receipt_persistence_recovery_idempotency_and_public_replay(self):
        candidate = self._candidate_value()
        endpoint = self.profile["rfc3161"]["endpoint"]
        response = trusted_time.TransportResponse(
            b"mock-rfc3161-response",
            200,
            "application/timestamp-reply",
            None,
            endpoint,
        )
        transport = mock.Mock(return_value=response)
        verification = self._verification_facts()
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = Path(tmp_dir)
            with (
                mock.patch.object(
                    trusted_time, "_candidate", return_value=(candidate, "ready")
                ),
                mock.patch.object(
                    trusted_time,
                    "verify_timestamp_response",
                    return_value=verification,
                ) as verify,
            ):
                status_path = trusted_time.poll_trusted_time_shadow(
                    runtime_root=runtime,
                    transport=transport,
                    clock=lambda: FIXED_NOW,
                    monotonic=iter((1.0, 1.25)).__next__,
                    nonce_factory=lambda: FIXED_NONCE,
                )
                receipt_path = (
                    runtime / "trusted_time_shadow_receipts" / "2026-08-27.json"
                )
                first_receipt = receipt_path.read_bytes()
                receipt_path.unlink()
                trusted_time.poll_trusted_time_shadow(
                    runtime_root=runtime,
                    transport=mock.Mock(
                        side_effect=AssertionError("existing link must avoid network")
                    ),
                    clock=lambda: FIXED_NOW,
                    nonce_factory=lambda: 2**255,
                )
                recovered_receipt = receipt_path.read_bytes()
                replayed = trusted_time.load_verified_trusted_time_receipt(
                    candidate.target, runtime_root=runtime
                )
                link_path = (
                    runtime / "trusted_time_shadow_response_links" / "2026-08-27.json"
                )
                canonical_link = link_path.read_bytes()
                link_path.write_text(
                    json.dumps(json.loads(canonical_link), indent=2), encoding="utf-8"
                )
                with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
                    trusted_time.load_verified_trusted_time_receipt(
                        candidate.target, runtime_root=runtime
                    )
                link_path.write_bytes(canonical_link)
                relocated_link = json.loads(canonical_link)
                source_object = runtime / relocated_link["response_object"]["path"]
                relocated_object = runtime / "relocated-response.tsr"
                relocated_object.write_bytes(source_object.read_bytes())
                relocated_link["response_object"]["path"] = (
                    relocated_object.relative_to(runtime).as_posix()
                )
                link_path.write_bytes(
                    trusted_time._canonical_bytes(relocated_link)  # noqa: SLF001
                )
                with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
                    trusted_time.load_verified_trusted_time_receipt(
                        candidate.target, runtime_root=runtime
                    )
                link_path.write_bytes(canonical_link)
            status = json.loads(status_path.read_text(encoding="utf-8"))

        self.assertEqual(first_receipt, recovered_receipt)
        self.assertTrue(replayed["rfc3161_receipt_verified"])
        self.assertTrue(status["cryptographic_time_shadow_verified"])
        self.assertFalse(status["trusted_anchor_receipt_verified"])
        self.assertFalse(status["e2_live_evidence_eligible"])
        transport.assert_called_once()
        self.assertGreaterEqual(verify.call_count, 5)

    def test_same_target_changed_semantics_and_raw_object_tamper_block(self):
        candidate = self._candidate_value()
        endpoint = self.profile["rfc3161"]["endpoint"]
        response = trusted_time.TransportResponse(
            b"mock-rfc3161-response",
            200,
            "application/timestamp-reply",
            None,
            endpoint,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = Path(tmp_dir)
            with (
                mock.patch.object(
                    trusted_time, "_candidate", return_value=(candidate, "ready")
                ),
                mock.patch.object(
                    trusted_time,
                    "verify_timestamp_response",
                    return_value=self._verification_facts(),
                ),
            ):
                trusted_time.poll_trusted_time_shadow(
                    runtime_root=runtime,
                    transport=lambda *_args: response,
                    clock=lambda: FIXED_NOW,
                    monotonic=iter((1.0, 1.1)).__next__,
                    nonce_factory=lambda: FIXED_NONCE,
                )
                with mock.patch.object(
                    trusted_time,
                    "_candidate",
                    return_value=(self._candidate_value("changed"), "ready"),
                ):
                    with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
                        trusted_time.poll_trusted_time_shadow(
                            runtime_root=runtime,
                            transport=lambda *_args: response,
                            clock=lambda: FIXED_NOW,
                        )
                    with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
                        trusted_time.load_verified_trusted_time_receipt(
                            candidate.target, runtime_root=runtime
                        )
                object_path = next(
                    (runtime / "trusted_time_shadow_objects" / "sha256").glob("*.tsr")
                )
                object_path.write_bytes(b"tampered")
                with self.assertRaises(trusted_time.TrustedTimeIntegrityError):
                    trusted_time.load_verified_trusted_time_receipt(
                        candidate.target, runtime_root=runtime
                    )

    def test_main_maps_busy_wait_and_integrity_exit_codes(self):
        cases = (
            (trusted_time.TrustedTimeBusyError("busy"), 3),
            (trusted_time.TrustedTimeNetworkError("offline"), 0),
            (trusted_time.TrustedTimeIntegrityError("tamper"), 2),
            (trusted_time.TrustedTimeConfigError("drift"), 2),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(
                    trusted_time, "poll_trusted_time_shadow", side_effect=error
                ):
                    self.assertEqual(trusted_time.main([]), expected)


if __name__ == "__main__":
    unittest.main()
