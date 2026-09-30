import xml.etree.ElementTree as ET

import pandas as pd

from src.analysis.feature_engineering import (
    calculate_ascend_speed,
    clean_ndl,
    extract_features,
)
from src.parsers.subsurface import extract_all_dive_profiles_refined

FIXTURE_PATH = "tests/fixtures/anonymized_subsurface_export.ssrf"


def _profile(legs, interval_s):
    """Build a depth profile from (duration_s, end_depth) legs, sampled every interval_s."""
    times, depths = [0.0], [0.0]
    t, d = 0.0, 0.0
    for duration, end in legs:
        steps = int(round(duration / interval_s))
        for i in range(1, steps + 1):
            times.append(t + i * interval_s)
            depths.append(d + (end - d) * i / steps)
        t, d = t + duration, end
    return times, depths


# Descend to 30 m, bottom time, ascend 30 -> 6 m at exactly 12 m/min (120 s),
# 3-minute safety stop at 5 m, then a 10 s sprint to the surface.
ONE_FAST_ASCENT = [(120, 30), (600, 30), (120, 6), (6, 5), (180, 5), (10, 0)]


def _dive(legs, interval_s, dive_number=1):
    times, depths = _profile(legs, interval_s)
    return pd.DataFrame(
        {"dive_number": [dive_number] * len(times), "time": times, "depth": depths}
    )


def test_ascent_rate_known_by_construction():
    result = calculate_ascend_speed(_dive(ONE_FAST_ASCENT, 10)).iloc[0]
    assert abs(result["max_ascend_speed"] - 12.0) < 0.01
    # One continuous fast ascent is one event, not one per sample.
    assert result["high_ascend_speed_count"] == 1


def test_ascent_metrics_independent_of_sample_rate():
    coarse = calculate_ascend_speed(_dive(ONE_FAST_ASCENT, 10)).iloc[0]
    fine = calculate_ascend_speed(_dive(ONE_FAST_ASCENT, 1)).iloc[0]
    assert abs(coarse["max_ascend_speed"] - fine["max_ascend_speed"]) < 0.01
    assert coarse["high_ascend_speed_count"] == fine["high_ascend_speed_count"] == 1


def test_sprint_from_the_stop_is_a_surfacing_bolt_not_a_sustained_one():
    # Slow 6 m/min ascent, 3-minute stop, then 5 m -> surface in 10 s.
    legs = [(120, 20), (300, 20), (150, 5), (180, 5), (10, 0)]
    result = calculate_ascend_speed(_dive(legs, 10)).iloc[0]
    assert result["max_ascend_speed"] < 10
    assert result["high_ascend_speed_count"] == 0
    # Timed from the 5 m mark to the 1 m surface line: 4 m in 8 s.
    assert abs(result["max_shallow_ascend_speed"] - 30.0) < 0.01
    assert result["shallow_bolt_count"] == 1


def test_controlled_surfacing_is_not_a_bolt():
    # Stop at 5 m, then a full minute to the surface (5 m/min).
    legs = [(120, 20), (300, 20), (150, 5), (180, 5), (60, 0)]
    result = calculate_ascend_speed(_dive(legs, 10)).iloc[0]
    assert abs(result["max_shallow_ascend_speed"] - 5.0) < 0.01
    assert result["shallow_bolt_count"] == 0


def test_surfacing_speed_independent_of_sample_rate():
    legs = [(120, 20), (300, 20), (150, 5), (180, 5), (10, 0)]
    coarse = calculate_ascend_speed(_dive(legs, 10)).iloc[0]
    fine = calculate_ascend_speed(_dive(legs, 1)).iloc[0]
    assert (
        abs(coarse["max_shallow_ascend_speed"] - fine["max_shallow_ascend_speed"])
        < 0.01
    )


def test_shallow_reef_swimming_is_not_a_bolt():
    # Up and down between 3 and 6 m for 40 minutes, then a slow finish.
    legs = [(60, 6)] + [(60, 3), (60, 6)] * 20 + [(60, 3), (90, 0)]
    result = calculate_ascend_speed(_dive(legs, 10)).iloc[0]
    assert result["shallow_bolt_count"] == 0
    assert result["max_shallow_ascend_speed"] < 5


def test_direct_ascent_from_depth_still_counts():
    # No safety stop: 20 m -> surface at 18 m/min. The deeper part must register.
    legs = [(120, 20), (300, 20), (66.67, 0)]
    result = calculate_ascend_speed(_dive(legs, 10)).iloc[0]
    assert result["max_ascend_speed"] > 15
    assert result["high_ascend_speed_count"] == 1
    # ...and its shallow end is a bolt to the surface too.
    assert result["max_shallow_ascend_speed"] > 15
    assert result["shallow_bolt_count"] == 1


def test_separate_fast_ascents_are_separate_events():
    legs = [(120, 30), (300, 30), (60, 18), (240, 18), (60, 6), (180, 5), (10, 0)]
    result = calculate_ascend_speed(_dive(legs, 10)).iloc[0]
    assert result["high_ascend_speed_count"] == 2


def test_unsorted_samples_give_the_same_answer():
    df = _dive(ONE_FAST_ASCENT, 10)
    shuffled = df.sample(frac=1, random_state=0)
    pd.testing.assert_frame_equal(
        calculate_ascend_speed(df), calculate_ascend_speed(shuffled)
    )


def test_extract_features():
    # Load the XML file
    with open(FIXTURE_PATH) as file:
        tree = ET.parse(file)
        root = tree.getroot()

    data = extract_all_dive_profiles_refined(root)
    features = extract_features(data)

    assert not features.empty, "The features dataframe is empty"
    expected_columns = {
        "dive_number",
        "avg_depth",
        "max_depth",
        "depth_variability",
        "avg_temp",
        "max_temp",
        "min_temp",
        "temp_gradient",
        "temp_variability",
        "avg_pressure",
        "max_pressure",
        "pressure_variability",
        "min_ndl",
        "entered_deco",
        "sac_rate",
        "max_ascend_speed",
        "high_ascend_speed_count",
        "max_shallow_ascend_speed",
        "shallow_bolt_count",
        "dive_site_name",
        "trip_name",
        "latitude",
        "longitude",
    }
    assert set(features.columns) == expected_columns

    # Values, not just shape: 9 dives flagged in_deco by the computer, one
    # dive without SAC, and a plausible ascent-rate range.
    assert len(features) == 144
    assert features["sac_rate"].isna().sum() == 1
    assert features["entered_deco"].sum() == 9
    assert features["max_ascend_speed"].between(0, 30).all()


def test_ndl_sentinel_zeros_are_not_readings():
    """Suunto-style 100 -> 0 -> 100 jumps are "not computed", not zero NDL."""
    data = pd.DataFrame(
        {
            "dive_number": [1] * 7 + [2] * 4,
            "time": [0, 10, 20, 30, 40, 50, 60, 0, 10, 20, 30],
            # dive 1: surface sentinel, mid-dive sentinel; dive 2: real countdown
            "ndl": [0, 100, 0, 100, 42, 0, 40, 9, 3, 1, 0],
        }
    )
    cleaned = clean_ndl(data)
    assert cleaned[data["dive_number"] == 1].min() == 40
    assert cleaned[data["dive_number"] == 2].tolist() == [9, 3, 1, 0]


def test_ndl_zero_after_a_gap_in_readings_counts_if_counting_down():
    data = pd.DataFrame(
        {"dive_number": [1] * 4, "time": [0, 10, 20, 30], "ndl": [2, None, None, 0]}
    )
    assert clean_ndl(data).tolist()[-1] == 0
