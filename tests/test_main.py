import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("pipeline_main", ROOT / "main.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load main.py")
pipeline = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pipeline
SPEC.loader.exec_module(pipeline)


class PipelineTests(unittest.TestCase):
    def test_default_selection_uses_all_stages_in_canonical_order(self):
        stages = pipeline.select_stages()

        self.assertEqual(stages, list(pipeline.STAGES))

    def test_selected_stages_are_deduplicated_and_canonically_ordered(self):
        stages = pipeline.select_stages(["fusion", "features", "fusion"])

        self.assertEqual([stage.name for stage in stages], ["features", "fusion"])

    def test_skipped_stages_are_removed(self):
        stages = pipeline.select_stages(skipped=["shap", "convlstm"])

        self.assertNotIn("shap", [stage.name for stage in stages])
        self.assertNotIn("convlstm", [stage.name for stage in stages])
        self.assertEqual(len(stages), len(pipeline.STAGES) - 2)

    def test_dry_run_does_not_start_subprocesses(self):
        calls = []

        pipeline.run_pipeline(
            pipeline.select_stages(["features"]),
            dry_run=True,
            runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        self.assertEqual(calls, [])

    def test_runner_uses_current_python_and_repository_root(self):
        calls = []

        def record(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        pipeline.run_pipeline(pipeline.select_stages(["features"]), runner=record)

        command, kwargs = calls[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], str(ROOT / "code" / "features.py"))
        self.assertEqual(kwargs, {"cwd": ROOT, "check": True})

    def test_failure_stops_later_stages_and_returns_exit_code(self):
        calls = []

        def fail_on_onset(command, **kwargs):
            calls.append(Path(command[1]).stem)
            if Path(command[1]).stem == "onset_analysis":
                raise subprocess.CalledProcessError(7, command)
            return subprocess.CompletedProcess(command, 0)

        exit_code = pipeline.main(
            ["--stage", "features", "--stage", "onset", "--stage", "shap"],
            runner=fail_on_onset,
        )

        self.assertEqual(exit_code, 7)
        self.assertEqual(calls, ["features", "onset_analysis"])

    def test_cli_rejects_unknown_stage(self):
        with self.assertRaises(SystemExit):
            pipeline.main(["--stage", "unknown"])


if __name__ == "__main__":
    unittest.main()
