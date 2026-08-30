import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "code" / "monitoring" / "ootang_epoch_settlement_cycle.py"
SPEC = importlib.util.spec_from_file_location(
    "ootang_epoch_settlement_cycle", MODULE_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load epoch settlement cycle")
cycle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cycle
SPEC.loader.exec_module(cycle)


@dataclass(frozen=True)
class _FakeResult:
    status: str
    progress: int
    current_source_derived_bounded_terminal_closure: bool = False


class EpochSettlementCycleTests(unittest.TestCase):
    def _stages(self, factory):
        return tuple(
            cycle.SettlementStage(name, factory(name))
            for name in cycle.EXPECTED_STAGE_ORDER
        )

    def _run(self, root: Path, stages):
        return cycle.coordinate_epoch_settlement_cycle(
            project_root=root,
            stages=stages,
            clock=lambda: datetime(2026, 8, 30, tzinfo=timezone.utc),
        )

    def test_reaches_existing_bounded_closure_in_one_stage_ordered_poll(self):
        calls = []
        counts = {name: 0 for name in cycle.EXPECTED_STAGE_ORDER}

        def factory(name):
            def run():
                calls.append(name)
                counts[name] += 1
                return _FakeResult(
                    status="current",
                    progress=counts[name],
                    current_source_derived_bounded_terminal_closure=(
                        name == "source_derived_bounded_terminal_closure"
                    ),
                )

            return run

        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), self._stages(factory))
            payload = json.loads(result.status_path.read_text())

        self.assertEqual(result.status, "bounded_terminal_closure_reached")
        self.assertEqual(result.passes, 1)
        self.assertEqual(calls, list(cycle.EXPECTED_STAGE_ORDER))
        self.assertTrue(payload["current_source_derived_bounded_terminal_closure"])
        self.assertFalse(payload["cache_authority"])
        self.assertFalse(payload["old_epoch_drained"])
        self.assertFalse(payload["lifecycle_authority"])

    def test_nonterminal_poll_calls_each_stage_once_and_yields_to_scheduler(self):
        counts = {name: 0 for name in cycle.EXPECTED_STAGE_ORDER}

        def factory(name):
            def run():
                counts[name] += 1
                return _FakeResult(status="waiting", progress=0)

            return run

        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), self._stages(factory))

        self.assertEqual(result.status, "settlement_poll_complete")
        self.assertEqual(result.passes, 1)
        self.assertTrue(all(count == 1 for count in counts.values()))
        self.assertFalse(result.current_source_derived_bounded_terminal_closure)

    def test_production_registry_resolves_exact_public_coordinators(self):
        stages = cycle._production_stages()

        self.assertEqual(
            tuple(stage.name for stage in stages), cycle.EXPECTED_STAGE_ORDER
        )
        self.assertEqual(len(stages), 16)
        for stage, (_, module_name, function_name) in zip(
            stages, cycle.PRODUCTION_STAGE_SPECS
        ):
            self.assertEqual(stage.run.__module__, f"monitoring.{module_name}")
            self.assertEqual(stage.run.__name__, function_name)


if __name__ == "__main__":
    unittest.main()
