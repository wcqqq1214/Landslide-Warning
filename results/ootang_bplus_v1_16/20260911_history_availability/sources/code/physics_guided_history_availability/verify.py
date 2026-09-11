"""Independent cell/difference/calendar checks; no refits or artifact writes."""

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
)
from physics_guided_rate_diagnostics.workflow import load_npz, no_model_runtime
from physics_guided_temporal_features.verify import Checks
from .core import POINTS, SOURCE_ROLE, UNKNOWN
from .workflow import CONFIG_SHA, PLAN_SHA, source_audit, source_guard, specification


def verify(out, sealed=True):
    spec = specification()
    if sealed:
        check_index(out)
        completed = read_json(out / "completed.json")
        if (
            not completed["execution_complete"]
            or not completed["numerical_verification"]
            or not 0 < completed["elapsed_seconds"] <= spec["timeout_seconds"]
            or read_json(out / "launcher.json")["exitcode"] != 0
        ):
            raise ValueError("Original history audit was not successful")
    manifest = read_json(out / "manifest.json")
    if (
        manifest["specification"] != spec
        or manifest["config_sha256"] != CONFIG_SHA
        or manifest["plan_sha256"] != PLAN_SHA
    ):
        raise ValueError("History audit specification differs")
    protected = source_guard(spec)
    if protected != read_json(out / "protected_before.json"):
        raise ValueError("Protected source inventory changed")
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    source_checks = source_audit(spec)
    if source_checks != read_json(out / "source_checks.json"):
        raise ValueError("Source identity audit differs")

    # A separate parser verifies every history value using raw row indices.
    raw = pd.read_csv(
        ROOT / spec["csv"],
        nrows=spec["max_label_prefix"],
        usecols=["Date", *[p + "/mm" for p in POINTS]],
        float_precision="round_trip",
    )
    calendar = pd.date_range("2016-07-01", periods=spec["max_label_prefix"])
    if raw.Date.tolist() != calendar.strftime("%Y-%m-%d").tolist():
        raise ValueError("Independent input calendar differs")
    labels = raw[[p + "/mm" for p in POINTS]].to_numpy(float)
    if not np.isfinite(labels).all():
        raise ValueError("Nonfinite independent prefix")
    tables = {
        k: pd.read_csv(out / (k + ".csv"), float_precision="round_trip")
        for k in spec["table_rows"]
    }
    if {k: len(v) for k, v in tables.items()} != spec["table_rows"]:
        raise ValueError("Audit table inventory differs")
    fingerprints = pd.read_csv(ROOT / spec["fingerprints"])
    u_check, du_check = Checks(spec["numeric_atol"]), Checks(spec["numeric_atol"])
    fingerprint_check = Checks(0.0)
    metadata, origin_rows, link_rows, history_rows = [], [], [], []
    for c in spec["origins"]:
        start = c - spec["history_days"]
        prediction_day = calendar[c - 1] + pd.Timedelta(days=1)
        expected_dates = calendar[start:c].strftime("%Y-%m-%d").to_numpy()
        saved = load_npz(out / f"history_{c}.npz")
        if set(saved) != {"dates", "u", "du"} or not np.array_equal(
            saved["dates"], expected_dates
        ):
            raise ValueError("Saved history schema or calendar differs")
        expected_u = np.array(
            [[labels[i, j] for j in range(4)] for i in range(start, c)]
        )
        expected_du = np.array(
            [
                [labels[i, j] - labels[i - 1, j] for j in range(4)]
                for i in range(start, c)
            ]
        )
        u_check.close(saved["u"], expected_u)
        du_check.close(saved["du"], expected_du)
        metadata.append(
            dict(
                origin=c,
                source_role=SOURCE_ROLE,
                raw_observation_as_of_verified=UNKNOWN,
                released_prefix_constructible=True,
                source_rows_read=c,
                history_days=spec["history_days"],
                history_start_index=start,
                history_end_index_exclusive=c,
                difference_left_index=start - 1,
                last_available_index=c - 1,
                point_order=list(POINTS),
                u_unit="mm",
                du_unit="mm/day; difference of materialized daily values",
            )
        )
        same_month_past = int(
            np.sum(calendar[:c].to_period("M") == prediction_day.to_period("M"))
        )
        remaining = len(
            pd.date_range(prediction_day, prediction_day + pd.offsets.MonthEnd(0))
        )
        origin_rows.append(
            dict(
                origin=c,
                first_forecast_date=prediction_day.strftime("%Y-%m-%d"),
                last_available_date=calendar[c - 1].strftime("%Y-%m-%d"),
                history_start_date=calendar[start].strftime("%Y-%m-%d"),
                difference_left_date=calendar[start - 1].strftime("%Y-%m-%d"),
                month=prediction_day.strftime("%Y-%m"),
                month_days=prediction_day.days_in_month,
                prior_same_month_rows=same_month_past,
                remaining_month_rows=remaining,
                crosses_monthly_piece=same_month_past > 0 and remaining > 0,
                raw_observation_as_of_verified=UNKNOWN,
            )
        )
        for point in POINTS:
            row = fingerprints[
                (fingerprints.month == prediction_day.strftime("%Y-%m"))
                & (fingerprints.column == point + "/mm")
            ]
            if len(row) != 1:
                raise ValueError("Ambiguous frozen monthly fingerprint")
            original = row.iloc[0]
            link_rows.append(
                dict(
                    origin=c,
                    month=prediction_day.strftime("%Y-%m"),
                    station=point,
                    n_days=int(original.n_days),
                    prior_same_month_rows=same_month_past,
                    remaining_month_rows=remaining,
                    max_abs_d4=float(original.max_abs_d4),
                    max_abs_cubic_residual=float(original.max_abs_cubic_residual),
                    month_passes_cubic_fingerprint=bool(
                        original.month_passes_cubic_fingerprint
                    ),
                    raw_observation_as_of_verified=UNKNOWN,
                )
            )
        for i in range(start, c):
            for j, point in enumerate(POINTS):
                history_rows.append(
                    dict(
                        origin=c,
                        source_index=i,
                        date=calendar[i].strftime("%Y-%m-%d"),
                        station=point,
                        u_mm=labels[i, j],
                        du_mm_per_day=labels[i, j] - labels[i - 1, j],
                    )
                )
    expected = {
        "origins": pd.DataFrame(origin_rows),
        "monthly_links": pd.DataFrame(link_rows),
        "history": pd.DataFrame(history_rows),
    }
    for key, frame in expected.items():
        numeric = {
            "history": ["u_mm", "du_mm_per_day"],
            "monthly_links": ["max_abs_d4", "max_abs_cubic_residual"],
        }.get(key, [])
        pd.testing.assert_frame_equal(
            tables[key].drop(columns=numeric),
            frame.drop(columns=numeric),
            check_exact=True,
        )
    u_check.close(tables["history"].u_mm, expected["history"].u_mm)
    du_check.close(tables["history"].du_mm_per_day, expected["history"].du_mm_per_day)
    fingerprint_check.close(
        tables["monthly_links"][["max_abs_d4", "max_abs_cubic_residual"]],
        expected["monthly_links"][["max_abs_d4", "max_abs_cubic_residual"]],
    )
    availability = read_json(out / "availability.json")
    if availability != dict(
        source_role=SOURCE_ROLE,
        released_prefix_constructible=True,
        raw_observation_as_of_verified=UNKNOWN,
        future_upstream_anchor_usage=UNKNOWN,
        interpretation="Conditional historical prototype on a released daily modeling series",
        origins=metadata,
    ):
        raise ValueError("Constructibility must not certify unknown raw availability")
    if read_json(out / "execution.json") != {
        k: v for k, v in spec.items() if k.startswith("new_")
    }:
        raise ValueError("Registered zero-fit execution differs")
    no_model_runtime()
    result = dict(
        passed=True,
        protected_files=len(protected),
        table_rows=spec["table_rows"],
        history_numeric_checks=u_check.values + du_check.values,
        max_abs_u_error_mm=u_check.maximum,
        max_abs_du_error_mm_per_day=du_check.maximum,
        reused_fingerprint_numeric_checks=fingerprint_check.values,
        max_abs_reused_fingerprint_error=fingerprint_check.maximum,
        source_numeric_comparisons=sum(
            v["compared_values"] for v in source_checks["comparisons"].values()
        ),
        raw_observation_as_of_verified=UNKNOWN,
    )
    if sealed and result != read_json(out / "verification.json"):
        raise ValueError("Sealed verification report differs")
    return result
