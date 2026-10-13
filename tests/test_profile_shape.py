"""Profile shape and gas: re-descents, surfacings, safety stop, reserve, thermocline."""

import numpy as np
import pandas as pd
import pytest

from src.analysis.feature_engineering import count_redescents, extract_features
from src.tools.dive import (
    anomaly_queries,
    dive_issues,
    habit_lines,
    profile_flags,
)


def _dive(depths, step: float = 10.0, **columns) -> pd.DataFrame:
    """One dive from a depth trace sampled every ``step`` seconds."""
    frame = pd.DataFrame(
        {
            "dive_number": "1",
            "time": np.arange(len(depths)) * step,
            "depth": np.asarray(depths, dtype=float),
            "temperature": np.nan,
            "pressure": np.nan,
            "ndl": np.nan,
            "sac_rate": np.nan,
        }
    )
    for name, values in columns.items():
        frame[name] = values
    return frame


def _square(bottom: float = 20.0, bottom_samples: int = 60, stop_samples: int = 18):
    """Down at 12 m/min, a bottom phase, up at 6 m/min with a stop at 5 m."""
    descent = list(np.arange(0, bottom, 2.0))
    ascent = list(np.arange(bottom, 5.0, -1.0))
    return [
        *descent,
        *[bottom] * bottom_samples,
        *ascent,
        *[5.0] * stop_samples,
        *SURFACING,
    ]


SURFACING = [4.0, 3.0, 2.0, 1.0, 0.0]  # 6 m/min from the stop


def _row(depths, **columns) -> pd.Series:
    return extract_features(_dive(depths, **columns)).iloc[0]


def test_redescents_count_sawtooth_teeth_not_reef_wobbles():
    assert count_redescents(np.array([0, 10, 20, 20, 12, 5, 0.0])) == 0
    assert count_redescents(np.array([0, 20, 12, 20, 12, 20, 5, 0.0])) == 2
    assert count_redescents(np.array([0, 20, 17, 20, 17, 20, 0.0])) == 0


def test_clean_square_profile_has_nothing_to_flag():
    row = _row(_square())
    assert row["safety_stop_minutes"] >= 3
    assert row["max_shallow_ascend_speed"] <= 10
    assert row["redescents"] == 0
    assert row["mid_dive_surfacings"] == 0
    assert row["max_descent_speed"] == pytest.approx(12.0)


def test_skipped_safety_stop_is_measured_short():
    assert _row(_square(stop_samples=2))["safety_stop_minutes"] < 1


def test_safety_stop_not_measured_when_the_log_ends_underwater():
    truncated = _square()[: -len(SURFACING)]  # the trace stops at 5 m
    assert np.isnan(_row(truncated)["safety_stop_minutes"])


def test_no_safety_stop_expected_on_a_shallow_dive():
    row = _row([0, 3, 6, 6, 6, 6, 6, 3, 0])
    assert np.isnan(row["safety_stop_minutes"])


def test_surfacing_and_going_back_down_is_counted():
    row = _row([0, 6, 12, 12, 6, 0, 0, 6, 12, 12, 6, 0])
    assert row["mid_dive_surfacings"] == 1
    assert row["redescents"] == 1


def test_bobbing_at_the_surface_before_the_dive_is_not_a_surfacing():
    row = _row([0, 1.5, 0.5, 1.5, 0.5, 6, 12, 12, 6, 0])
    assert row["mid_dive_surfacings"] == 0


def test_end_pressure_is_the_last_reading_underwater():
    depths = _square()
    pressure = np.full(len(depths), np.nan)
    pressure[0] = 200
    pressure[-3] = 42  # at 2 m, on the way up
    pressure[-1] = 3  # on the boat, regulator purged
    assert _row(depths, pressure=pressure)["end_pressure"] == 42


def test_no_tank_pod_leaves_end_pressure_unrecorded():
    assert np.isnan(_row(_square())["end_pressure"])


def test_sensor_cooling_from_air_is_not_a_thermocline():
    # 30°C on the boat, settling to 20°C water while the diver sits at depth.
    depths = _square()
    temp = np.full(len(depths), np.nan)
    for i, value in enumerate([30, 28, 26, 24, 22, 21, 20]):
        temp[i * 3] = value
    row = _row(depths, temperature=temp)
    assert row["max_temp"] - row["min_temp"] == 10
    assert row["temp_gradient"] == 0


def test_thermocline_is_the_gap_between_shallows_and_bottom():
    depths = _square(bottom=25.0)
    temp = [18.0 if d >= 20 else 26.0 for d in depths]
    assert _row(depths, temperature=temp)["temp_gradient"] == 8


def test_thermocline_not_measured_without_temperature():
    assert np.isnan(_row(_square())["temp_gradient"])


# --- Flags, issues and what the model is told ---------------------------------


def _flagged(**values) -> dict:
    base = {
        "dive_number": "1",
        "max_depth": 25.0,
        "end_pressure": 80.0,
        "safety_stop_minutes": 4.0,
        "redescents": 0,
        "mid_dive_surfacings": 0,
        "max_descent_speed": 12.0,
        "technical": False,
        "entered_deco": False,
    }
    return {**base, **values}


def test_clean_dive_raises_no_profile_flag():
    assert not any(profile_flags(_flagged()).values())


def test_each_habit_raises_its_own_flag():
    assert profile_flags(_flagged(end_pressure=20.0))["low_reserve"]
    assert profile_flags(_flagged(safety_stop_minutes=0.7))["short_stop"]
    assert profile_flags(_flagged(redescents=3))["sawtooth"]
    assert profile_flags(_flagged(mid_dive_surfacings=1))["surfaced_mid_dive"]
    assert profile_flags(_flagged(max_descent_speed=23.0))["fast_descent"]


def test_unrecorded_values_raise_no_flag():
    row = _flagged(end_pressure=np.nan, safety_stop_minutes=np.nan)
    assert not any(profile_flags(row).values())


def test_technical_dive_is_not_judged_on_its_last_tank_reading():
    assert not profile_flags(_flagged(end_pressure=20.0, technical=True))["low_reserve"]


def test_deco_dive_is_judged_by_its_deco_stops_not_the_safety_stop():
    row = _flagged(safety_stop_minutes=0.5, entered_deco=True)
    assert not profile_flags(row)["short_stop"]


def test_drills_on_a_shallow_dive_are_not_a_sawtooth():
    row = _flagged(max_depth=7.0, redescents=4, mid_dive_surfacings=3)
    flags = profile_flags(row)
    assert not flags["sawtooth"]
    assert not flags["surfaced_mid_dive"]


def test_dive_issues_name_the_number_behind_each_flag():
    issues = dive_issues(_flagged(end_pressure=15.0, safety_stop_minutes=0.7))
    assert any(i.startswith("LOW GAS RESERVE") and "15 bar" in i for i in issues)
    assert any(i.startswith("SHORT SAFETY STOP") and "0.7 min" in i for i in issues)


def _log() -> pd.DataFrame:
    """Four dives: three end low on gas, one of those also has no safety stop."""
    dives = []
    for number, (end, stop_samples) in enumerate(
        [(80, 18), (30, 18), (25, 18), (10, 2)], start=1
    ):
        depths = _square(stop_samples=stop_samples)
        pressure = np.full(len(depths), np.nan)
        pressure[0], pressure[-3] = 200, end
        dives.append(
            _dive(depths, pressure=pressure).assign(
                dive_number=str(number), dive_site_name="House reef"
            )
        )
    return pd.concat(dives, ignore_index=True)


def test_habits_rank_what_the_log_repeats_first():
    lines = habit_lines(extract_features(_log()))
    assert lines[0] == "ended below 50 bar: 3/4 dives (75%)"
    assert "safety stop under 3 min: 1/4 dives (25%)" in lines
    assert not any("ascent" in line for line in lines)


def test_anomaly_queries_cover_gas_and_stops():
    queries = " | ".join(anomaly_queries(extract_features(_log())))
    assert "low on gas" in queries
    assert "omitted safety stop" in queries


def test_dive_line_flags_reserve_and_stop_for_the_model():
    from src.agent.conversation import _dive_line

    features = extract_features(_log())
    clean, worst = _dive_line(features.iloc[0]), _dive_line(features.iloc[3])
    assert "ended on 80bar" in clean
    assert "LOW RESERVE" not in clean
    assert "LOW RESERVE 10bar" in worst
    assert "SHORT STOP" in worst


def test_seed_context_carries_the_habits_line():
    from src.agent.conversation import DiverRoastAgent

    agent = DiverRoastAgent()
    agent.set_dive_data(_log())
    context = agent.history[0].parts[0].text
    assert "ended below 50 bar: 3/4 dives (75%)" in context
    assert "ended below 50 bar" in agent.log_facts
