"""Verify contracts and saved mean provenance without scoring new candidates."""

import io
import unittest

import numpy as np

from .core import (
    B,
    HALF,
    OLD,
    REG,
    ROOT,
    guard_sources,
    lock,
    sha,
    spec,
    utc,
    write_json,
)
from .run import assemble


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    dest = root / "implementation_verification"
    dest.mkdir(parents=True, exist_ok=False)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), pattern="test_transformer_calibration.py"
    )
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (dest / "contracts.log").write_text(stream.getvalue())
    print(stream.getvalue())
    if not result.wasSuccessful():
        raise AssertionError("Preflight contracts failed")
    count = guard_sources()
    checks = []
    for phase, (n, end) in cfg["stages"].items():
        means, full, seeds, dates = assemble(cfg, phase)
        assert dates[0] == "2016-07-01"
        assert np.array_equal(
            np.diff(dates.astype("datetime64[D]")),
            np.ones(end - 1, dtype="timedelta64[D]"),
        )
        for m in (OLD, REG, HALF):
            np.testing.assert_allclose(
                full[m],
                np.mean([seeds[f"{m}__{s}"] for s in cfg["seeds"]], axis=0),
                atol=1e-9,
                rtol=0,
            )
        np.testing.assert_allclose(
            full[HALF] - full[B], 0.5 * (full[OLD] - full[B]), atol=1e-9, rtol=0
        )
        assert len(dates[n:]) == end - n
        checks.append(
            dict(
                phase=phase,
                training_prefix=n,
                start=str(dates[n]),
                end=str(dates[-1]),
                all_finite=all(np.isfinite(a).all() for a in means.values()),
            )
        )
    paths = [p for p in (ROOT / "code/transformer_calibration").glob("*.py")]
    paths += [ROOT / "tests/test_transformer_calibration.py"]
    write_json(
        root / "implementation_lock.json",
        dict(time_utc=utc(), files={str(p.relative_to(ROOT)): sha(p) for p in paths}),
    )
    write_json(
        dest / "receipt.json",
        dict(
            status="passed",
            time_utc=utc(),
            tests_run=result.testsRun,
            source_files_checked=count,
            date_and_mean_checks=checks,
            new_fits=0,
            optimizer_updates=0,
            candidate_scores_evaluated=0,
            full_label_reads=0,
        ),
    )
    lock(dest, "manifest.json", list(dest.iterdir()))


if __name__ == "__main__":
    main()
