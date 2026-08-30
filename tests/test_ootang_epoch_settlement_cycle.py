import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
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
    current_source_derived_bounded_terminal_closure: bool = False


class EpochSettlementCycleTests(unittest.TestCase):
    def _stages(self, factory):
        return tuple(
            cycle.SettlementStage(stage.name, factory(stage.name))
            for stage in cycle.PRODUCTION_STAGES
        )

    def _run(self, root: Path, stages):
        return cycle.coordinate_epoch_settlement_cycle(
            status_path=root / "status.json",
            stages=stages,
        )

    def test_reaches_existing_bounded_closure_in_one_stage_ordered_poll(self):
        calls = []
        names = [stage.name for stage in cycle.PRODUCTION_STAGES]
        counts = dict.fromkeys(names, 0)

        def factory(name):
            def run():
                calls.append(name)
                counts[name] += 1
                return _FakeResult(
                    status="current",
                    current_source_derived_bounded_terminal_closure=(
                        name == "source_derived_bounded_terminal_closure"
                    ),
                )

            return run

        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), self._stages(factory))
            payload = json.loads(result.status_path.read_text())

        self.assertEqual(result.status, "bounded_terminal_closure_reached")
        self.assertEqual(calls, names)
        self.assertTrue(payload["current_source_derived_bounded_terminal_closure"])
        self.assertFalse(payload["cache_authority"])
        self.assertEqual([item["stage"] for item in payload["stages"]], names)

    def test_nonterminal_poll_calls_each_stage_once_and_yields_to_scheduler(self):
        names = [stage.name for stage in cycle.PRODUCTION_STAGES]
        counts = dict.fromkeys(names, 0)

        def factory(name):
            def run():
                counts[name] += 1
                return _FakeResult(status="waiting")

            return run

        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), self._stages(factory))

        self.assertEqual(result.status, "settlement_poll_complete")
        self.assertTrue(all(count == 1 for count in counts.values()))
        self.assertFalse(result.current_source_derived_bounded_terminal_closure)


if __name__ == "__main__":
    unittest.main()
