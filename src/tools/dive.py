"""Dive-log tools: per-dive and whole-log analysis.

Every function takes the parsed per-sample DataFrame and, optionally, the
features already computed from the *whole* log. Single-dive answers read the
same features row as the whole-log view, so the two can't disagree.

Metrics the log didn't record are NaN in the features. They're never
compared against a threshold and never printed as a number.
"""

import contextlib
import math
import re

import pandas as pd

from src.analysis.feature_engineering import (
    ASCENT_LIMIT_M_MIN,
    ASCENT_WINDOW_S,
    COLD_WATER_C,
    GAS_DENSITY_MAX_G_L,
    GAS_DENSITY_RECOMMENDED_G_L,
    PO2_LIMIT_BAR,
    PO2_WORKING_BAR,
    REDESCENT_M,
    SHALLOW_ASCENT_LIMIT_M_MIN,
    SHALLOW_ZONE_M,
    STOP_EXPECTED_BELOW_M,
    data_coverage,
    extract_features,
)

NDL_DANGER_MIN = 5.0
SAC_HIGH_L_MIN = 20.0
DEEP_M = 30.0
# Thermal screening thresholds (heuristics, not DAN limits). The log has the
# water temperature, not the diver's suit or body temperature.
PROLONGED_COLD_MIN = 30.0  # minutes below COLD_WATER_C: hypothermia risk
WARM_WATER_C = 29.0  # coldest in-water reading at or above this: warm dive
LONG_WARM_MIN = 60.0  # ...for this long: dehydration, a DCS factor

# Profile and gas screening thresholds (diving conventions and heuristics, not
# DAN limits). The log has the last tank reading, not how the gas was planned.
RESERVE_BAR = 50.0  # the usual reserve to surface with
SAFETY_STOP_MIN = 3.0  # the usual stop: 3 min at 5 m
SAWTOOTH_REDESCENTS = 2
FAST_DESCENT_M_MIN = 18.0
THERMOCLINE_C = 3.0  # a gap this size between shallows and bottom is worth a mention


# Underwater time above a required stop shorter than this is sensor noise or
# waves, not a missed stop.
MISSED_STOP_MIN = 0.5


def deco_flags(row) -> dict[str, bool]:
    """How a dive's deco should be judged.

    A technical dive (trimix, deco gas, rebreather) plans its deco, so deco
    entry is only an issue on a recreational dive. Missing a required stop,
    or surfacing with one still owed, is serious on any dive. Unrecorded
    stop depths never raise a flag.
    """
    technical = bool(row.get("technical"))
    missed = row.get("missed_stop_minutes")
    owing = row.get("surfaced_owing")
    missed_stop = measured(missed) and missed >= MISSED_STOP_MIN
    surfaced_owing = measured(owing) and owing > 0
    entered = bool(row.get("entered_deco"))
    return {
        "technical": technical,
        "recreational_deco": entered and not technical,
        "missed_stop": missed_stop,
        "surfaced_owing": surfaced_owing,
        "stops_kept": entered
        and measured(missed)
        and not missed_stop
        and not surfaced_owing,
    }


def thermal_flags(row) -> dict[str, bool]:
    """Which thermal risks the measured data supports for one dive.

    NaN (temperature not recorded) never raises a flag.
    """
    cold = row.get("cold_minutes")
    stop = row.get("stop_temp")
    water = row.get("water_min_temp")
    minutes = row.get("dive_minutes")
    return {
        "prolonged_cold": measured(cold) and cold >= PROLONGED_COLD_MIN,
        "cold_stops": measured(stop) and stop < COLD_WATER_C,
        "long_warm": measured(water)
        and water >= WARM_WATER_C
        and measured(minutes)
        and minutes >= LONG_WARM_MIN,
    }


def profile_flags(row) -> dict[str, bool]:
    """Which gas and profile-shape habits the measured data supports for one dive.

    NaN never raises a flag. A technical dive carries gas the log may not
    show, so its last tank reading isn't judged; a dive in deco is judged by
    its deco stops, not by the safety stop. Re-descents and surfacings only
    count on dives deeper than ``STOP_EXPECTED_BELOW_M``: on a shallow
    training dive they are drills.
    """
    end = row.get("end_pressure")
    stop = row.get("safety_stop_minutes")
    descent = row.get("max_descent_speed")
    redescents = row.get("redescents")
    surfacings = row.get("mid_dive_surfacings")
    deep_enough = (row.get("max_depth") or 0) > STOP_EXPECTED_BELOW_M
    return {
        "low_reserve": measured(end)
        and end < RESERVE_BAR
        and not bool(row.get("technical")),
        "short_stop": measured(stop)
        and stop < SAFETY_STOP_MIN
        and not bool(row.get("entered_deco")),
        "sawtooth": deep_enough
        and measured(redescents)
        and redescents >= SAWTOOTH_REDESCENTS,
        "surfaced_mid_dive": deep_enough and measured(surfacings) and surfacings >= 1,
        "fast_descent": measured(descent) and descent > FAST_DESCENT_M_MIN,
    }


# What the log measures falls into three classes:
#   LIMIT   - a rule any diver is held to: an ascent limit, a required deco
#             stop, the oxygen limit of the gas breathed.
#   MARGIN  - how close the diver ran to a limit or a recommendation. It
#             depends on training and plan, so it is a choice to remark on,
#             never a limit broken. Gas density is here too: air at 40 m is
#             past the researched 6.2 g/L maximum, yet inside what every
#             agency trains divers to do.
#   everything else is descriptive (depth on its own, air consumption,
#   temperature, descent rate, a low NDL, deco with the stops kept): a fact
#   about the diver, with no good or bad.
LIMIT = "limit"
MARGIN = "margin"
RECREATIONAL_MAX_M = 40.0  # beyond the range recreational training covers
NDL_MARGIN_MIN = 10.0
RESERVE_THIN_BAR = 30.0
SAFETY_STOP_THIN_MIN = 1.0
SAWTOOTH_MANY_REDESCENTS = 3
SUSTAINED_ASCENT_WATCH_M_MIN = 9.0  # the stricter of the two usual ascent limits
BOLT_HARD_M_MIN = 15.0


def gas_flags(row) -> dict[str, bool]:
    """How the gas at the deepest point sits against its limits. NaN never flags."""
    po2 = row.get("max_po2")
    density = row.get("gas_density")
    return {
        "po2_over_working": measured(po2) and po2 > PO2_WORKING_BAR,
        "po2_over_limit": measured(po2) and po2 > PO2_LIMIT_BAR,
        "density_over_recommended": measured(density)
        and density > GAS_DENSITY_RECOMMENDED_G_L,
        "density_over_max": measured(density) and density > GAS_DENSITY_MAX_G_L,
    }


def dive_findings(row) -> dict[str, tuple[str, float]]:
    """What a dive is judged on: {finding: (class, points)}.

    A limit scores its weight (2, or 3 for deco and oxygen) times how far
    past the limit the dive went; a margin scores 1 or 2. Descriptive facts
    and unrecorded metrics score nothing and aren't findings.
    """
    found: dict[str, tuple[str, float]] = {}
    deco = deco_flags(row)
    gas = gas_flags(row)
    profile = profile_flags(row)
    ascent = row.get("max_ascend_speed")
    surfacing = row.get("max_shallow_ascend_speed")

    # Limits
    if deco["missed_stop"] or deco["surfaced_owing"]:
        found["missed deco stop"] = (LIMIT, 9.0)
    if gas["po2_over_limit"]:
        found["oxygen pressure"] = (LIMIT, 6.0)
    elif gas["po2_over_working"]:
        found["oxygen pressure"] = (LIMIT, 3.0)
    if measured(ascent) and ascent > ASCENT_LIMIT_M_MIN:
        found["rapid ascent"] = (LIMIT, 4.0)
    elif measured(ascent) and ascent > SUSTAINED_ASCENT_WATCH_M_MIN:
        found["rapid ascent"] = (LIMIT, 2.0)
    if measured(surfacing) and surfacing > BOLT_HARD_M_MIN:
        found["bolted to surface"] = (LIMIT, 4.0)
    elif measured(surfacing) and surfacing > SHALLOW_ASCENT_LIMIT_M_MIN:
        found["bolted to surface"] = (LIMIT, 2.0)

    # Margins. Running the NDL down, or going into deco and keeping the
    # stops, is not one of them: nothing was cut close that the computer
    # didn't then have the diver pay back at the stops.
    if profile["low_reserve"]:
        thin = row["end_pressure"] < RESERVE_THIN_BAR
        found["low gas reserve"] = (MARGIN, 2.0 if thin else 1.0)
    if profile["short_stop"]:
        thin = row["safety_stop_minutes"] < SAFETY_STOP_THIN_MIN
        found["short safety stop"] = (MARGIN, 2.0 if thin else 1.0)
    if profile["surfaced_mid_dive"]:
        found["surfaced mid-dive"] = (MARGIN, 2.0)
    if profile["sawtooth"]:
        many = row["redescents"] >= SAWTOOTH_MANY_REDESCENTS
        found["sawtooth profile"] = (MARGIN, 2.0 if many else 1.0)
    if gas["density_over_recommended"]:
        found["gas density"] = (MARGIN, 2.0 if gas["density_over_max"] else 1.0)
    if (row.get("max_depth") or 0) > RECREATIONAL_MAX_M and not deco["technical"]:
        found["deeper than 40 m"] = (MARGIN, 1.0)
    return found


def measured(value) -> bool:
    """True if the value was actually recorded (not None/NaN)."""
    if value is None:
        return False
    with contextlib.suppress(TypeError, ValueError):
        return not math.isnan(float(value))
    return False


def fmt(value, spec: str = ".1f", unit: str = "") -> str:
    """Format a metric, or say it wasn't recorded."""
    if not measured(value):
        return "not recorded"
    return f"{float(value):{spec}}{unit}"


_UNNUMBERED = re.compile(r"^unnum_(\d{4}-\d{2}-\d{2})_?(\d{2})?:?(\d{2})?")


def dive_label(dive_number) -> str:
    """How a dive is shown to the model: "#12", or its date and time.

    Dives the computer didn't number get an internal id ("unnum_2025-10-14_
    154315"); shown as is, models quote it in the roast.
    """
    match = _UNNUMBERED.match(str(dive_number))
    if not match:
        return f"#{dive_number}"
    date, hour, minute = match.groups()
    return f"{date} {hour}:{minute}" if hour and minute else date


def dive_sort_key(dive_number) -> tuple:
    """Numbered dives in numeric order, then unnumbered ones by date/time."""
    s = str(dive_number)
    if re.fullmatch(r"\d+", s):
        return (0, int(s), s)
    return (1, 0, s)


def filter_dive(df: pd.DataFrame, dive_number) -> pd.DataFrame:
    """Rows for one dive, matching the ID as string or int."""
    result = df[df["dive_number"].astype(str) == str(dive_number)]
    if result.empty:
        with contextlib.suppress(ValueError, TypeError):
            result = df[df["dive_number"] == int(dive_number)]
    return result


def _features_row(
    df: pd.DataFrame, dive_number, features: pd.DataFrame | None
) -> pd.Series | None:
    if features is None:
        features = extract_features(df)
    rows = features[features["dive_number"].astype(str) == str(dive_number)]
    return None if rows.empty else rows.iloc[0]


def coverage_line(features: pd.DataFrame) -> str:
    """One line telling the model which metrics the log actually has."""
    c = data_coverage(features)
    n = c["dives"]
    return (
        f"Data coverage: SAC available for {c['sac']}/{n} dives · "
        f"NDL for {c['ndl']}/{n} · tank pressure for {c['pressure']}/{n}. "
        f"Metrics marked 'not recorded' were "
        f"not logged by the dive computer; don't estimate them."
    )


def _location(row) -> str:
    site = str(row.get("dive_site_name") or "N/A")
    trip = str(row.get("trip_name") or "N/A")
    location = site if site != "N/A" else "unknown site"
    if trip != "N/A" and trip != site:
        location += f" ({trip})"
    return location


def describe_features(row) -> str:
    """Natural-language summary of a dive's measured metrics only."""
    parts = [
        f"Average depth {fmt(row.get('avg_depth'), unit=' m')}",
        f"maximum depth {fmt(row.get('max_depth'), unit=' m')}",
        f"max ascent rate {fmt(row.get('max_ascend_speed'), unit=' m/min')} "
        f"({ASCENT_WINDOW_S:.0f} s average)",
        f"fast-ascent episodes {int(row.get('high_ascend_speed_count') or 0)}",
        f"fastest surfacing through the last {SHALLOW_ZONE_M:.0f} m "
        f"{fmt(row.get('max_shallow_ascend_speed'), unit=' m/min')}",
        f"fast surfacings {int(row.get('shallow_bolt_count') or 0)}",
    ]
    if measured(row.get("min_ndl")):
        parts.append(f"minimum NDL {fmt(row['min_ndl'], '.0f', ' min')}")
    if measured(row.get("sac_rate")):
        parts.append(f"SAC rate {fmt(row['sac_rate'], unit=' L/min')}")
    if measured(row.get("avg_temp")):
        parts.append(f"average temperature {fmt(row['avg_temp'], unit=' °C')}")
    if measured(row.get("dive_minutes")):
        parts.append(f"duration {fmt(row['dive_minutes'], '.0f', ' min')}")
    if measured(row.get("water_min_temp")):
        parts.append(f"coldest water {fmt(row['water_min_temp'], unit=' °C')}")
    if measured(row.get("cold_minutes")) and row["cold_minutes"] > 0:
        parts.append(
            f"{fmt(row['cold_minutes'], '.0f', ' min')} below {COLD_WATER_C:.0f} °C"
        )
    if measured(row.get("stop_temp")):
        parts.append(
            f"water during ascent and stops {fmt(row['stop_temp'], unit=' °C')}"
        )
    if measured(row.get("temp_gradient")):
        parts.append(
            f"thermocline {fmt(row['temp_gradient'], unit=' °C')} "
            f"(shallows warmer than the bottom)"
        )
    if measured(row.get("max_po2")):
        parts.append(
            f"oxygen partial pressure at the deepest point "
            f"{fmt(row['max_po2'], '.2f', ' bar')} on {row['min_o2']:.0f}% oxygen"
        )
    if measured(row.get("gas_density")):
        parts.append(
            f"gas density at the deepest point {fmt(row['gas_density'], unit=' g/L')}"
        )
    if measured(row.get("max_descent_speed")):
        parts.append(
            f"max descent rate {fmt(row['max_descent_speed'], unit=' m/min')} "
            f"({ASCENT_WINDOW_S:.0f} s average)"
        )
    if measured(row.get("redescents")):
        parts.append(
            f"re-descents of {REDESCENT_M:.0f} m or more {int(row['redescents'])}"
        )
    if measured(row.get("mid_dive_surfacings")):
        parts.append(f"mid-dive surfacings {int(row['mid_dive_surfacings'])}")
    if measured(row.get("safety_stop_minutes")):
        parts.append(
            f"time at the safety stop {fmt(row['safety_stop_minutes'], unit=' min')}"
        )
    if measured(row.get("end_pressure")):
        parts.append(
            f"last tank reading underwater {fmt(row['end_pressure'], '.0f', ' bar')}"
        )
    if row.get("technical"):
        parts.append(f"technical dive ({row.get('tech_reason')})")
    if row.get("entered_deco"):
        parts.append(f"decompression {fmt(row.get('deco_minutes'), '.0f', ' min')}")
        deco = deco_flags(row)
        if deco["stops_kept"]:
            parts.append("all required stops kept")
        if deco["missed_stop"]:
            parts.append(
                f"{fmt(row['missed_stop_minutes'], unit=' min')} above a required stop"
            )
        if deco["surfaced_owing"]:
            parts.append(
                f"surfaced with a {row['surfaced_owing']:.0f} m stop still owed"
            )
    return ", ".join(parts) + "."


def _not_recorded(row) -> list[str]:
    names = {
        "sac_rate": "SAC rate",
        "min_ndl": "NDL",
        "avg_pressure": "tank pressure",
    }
    return [label for col, label in names.items() if not measured(row.get(col))]


def dive_issues(row) -> list[str]:
    """Safety issues supported by measured data for one features row."""
    issues = []
    if row.get("max_ascend_speed", 0) > ASCENT_LIMIT_M_MIN:
        episodes = int(row.get("high_ascend_speed_count") or 0)
        issues.append(
            f"HIGH ASCENT RATE: Max ascent rate was {row['max_ascend_speed']:.1f} m/min "
            f"averaged over {ASCENT_WINDOW_S:.0f} s (recommended: <{ASCENT_LIMIT_M_MIN:.0f} "
            f"m/min), in {episodes} separate fast-ascent episode(s)."
        )
    if row.get("max_shallow_ascend_speed", 0) > SHALLOW_ASCENT_LIMIT_M_MIN:
        bolts = int(row.get("shallow_bolt_count") or 0)
        issues.append(
            f"BOLTED TO THE SURFACE: fastest approach through the last "
            f"{SHALLOW_ZONE_M:.0f} m was {row['max_shallow_ascend_speed']:.1f} m/min "
            f"(recommended: <{SHALLOW_ASCENT_LIMIT_M_MIN:.0f} m/min), on {bolts} "
            f"surfacing(s). The relative pressure drop is largest in the last metres "
            f"({SHALLOW_ZONE_M:.0f} m to the surface is {1 + SHALLOW_ZONE_M / 10:.1f} to "
            f"1.0 bar), so leaving the safety stop is where a fast ascent matters most."
        )
    deco = deco_flags(row)
    if deco["surfaced_owing"]:
        issues.append(
            f"SURFACED WITH DECO OWED: the computer still showed a "
            f"{row['surfaced_owing']:.0f} m stop at the surface. Skipping a required "
            f"stop is a direct DCS risk."
        )
    if deco["missed_stop"]:
        issues.append(
            f"MISSED DECO STOP: {row['missed_stop_minutes']:.1f} min underwater above "
            f"the stop the computer required, up to {row['max_above_stop']:.1f} m "
            f"above it."
        )
    if deco["recreational_deco"]:
        kept = (
            " The required stops were kept."
            if deco["stops_kept"]
            else ""
            if measured(row.get("missed_stop_minutes"))
            else " Stop depths weren't recorded, so whether the stops were kept is unknown."
        )
        issues.append(
            f"ENTERED DECOMPRESSION on a recreational dive (no helium, deco gas or "
            f"rebreather in the log): {fmt(row.get('deco_minutes'), '.0f', ' min')} "
            f"of deco obligation.{kept}"
        )
    gas = gas_flags(row)
    if gas["po2_over_working"]:
        over = (
            f"over the {PO2_LIMIT_BAR:.1f} bar contingency limit"
            if gas["po2_over_limit"]
            else f"over the {PO2_WORKING_BAR:.1f} bar working limit "
            f"(contingency limit {PO2_LIMIT_BAR:.1f} bar)"
        )
        issues.append(
            f"OXYGEN PRESSURE: {row['max_po2']:.2f} bar at {row['max_depth']:.1f} m "
            f"on {row['min_o2']:.0f}% oxygen, {over}. Oxygen toxicity risk rises "
            f"with oxygen pressure."
        )
    if gas["density_over_max"]:
        issues.append(
            f"THIN MARGIN, GAS DENSITY: {row['gas_density']:.1f} g/L at the deepest "
            f"point, over the {GAS_DENSITY_MAX_G_L:.1f} g/L that research gives as "
            f"the maximum. Dense gas is harder to breathe and CO2 builds up."
        )
    elif gas["density_over_recommended"]:
        issues.append(
            f"THIN MARGIN, GAS DENSITY: {row['gas_density']:.1f} g/L at the deepest "
            f"point, above the recommended {GAS_DENSITY_RECOMMENDED_G_L:.1f} g/L and "
            f"under the {GAS_DENSITY_MAX_G_L:.1f} g/L maximum."
        )
    profile = profile_flags(row)
    if profile["low_reserve"]:
        issues.append(
            f"THIN MARGIN, GAS RESERVE: the last tank reading underwater was "
            f"{row['end_pressure']:.0f} bar, below the usual {RESERVE_BAR:.0f} bar "
            f"reserve. The log doesn't show the tank size or the plan."
        )
    if profile["short_stop"]:
        issues.append(
            f"THIN MARGIN, SAFETY STOP: {row['safety_stop_minutes']:.1f} min between 3 and "
            f"6 m before surfacing from {row['max_depth']:.1f} m (usual: "
            f"{SAFETY_STOP_MIN:.0f} min at 5 m)."
        )
    if profile["surfaced_mid_dive"]:
        issues.append(
            f"THIN MARGIN, SURFACED MID-DIVE: came up to the surface and went back down "
            f"{int(row['mid_dive_surfacings'])} time(s)."
        )
    if profile["sawtooth"]:
        issues.append(
            f"THIN MARGIN, SAWTOOTH PROFILE: {int(row['redescents'])} re-descents of "
            f"{REDESCENT_M:.0f} m or more after ascending."
        )
    flags = thermal_flags(row)
    if flags["prolonged_cold"]:
        issues.append(
            f"PROLONGED COLD EXPOSURE: {row['cold_minutes']:.0f} min in water below "
            f"{COLD_WATER_C:.0f} °C. Hypothermia risk depends on thermal protection, "
            f"which the log doesn't record."
        )
    if flags["cold_stops"]:
        issues.append(
            f"COLD DECOMPRESSION PHASE: water during the ascent and stops was "
            f"{row['stop_temp']:.1f} °C. Being cold while decompressing slows inert "
            f"gas washout and raises DCS risk."
        )
    if flags["long_warm"]:
        issues.append(
            f"LONG WARM-WATER DIVE: {row['dive_minutes']:.0f} min in water no colder "
            f"than {row['water_min_temp']:.1f} °C. Long, warm dives dehydrate, and "
            f"dehydration is a DCS risk factor; hydration isn't in the log."
        )
    return issues


def analyze_dive_profile(
    df: pd.DataFrame, dive_number, features: pd.DataFrame | None = None
) -> str:
    """Analyze one dive's profile and flag safety issues."""
    if filter_dive(df, dive_number).empty:
        return f"No data found for dive number {dive_number}."
    if features is None:
        features = extract_features(df)
    row = _features_row(df, dive_number, features)
    if row is None:
        return f"Could not extract features for dive {dive_number}."

    lines = [f"Dive {dive_number} Analysis ({_location(row)}):", describe_features(row)]
    missing = _not_recorded(row)
    if missing:
        lines.append(f"Not recorded for this dive: {', '.join(missing)}.")
    issues = dive_issues(row)
    if issues:
        lines += ["", "Issues Found:", *(f"- {issue}" for issue in issues)]
    else:
        lines += ["", "No major safety issues detected in the recorded data."]
    lines += ["", coverage_line(features)]
    return "\n".join(lines)


def get_dive_summary(
    df: pd.DataFrame, dive_number, features: pd.DataFrame | None = None
) -> str:
    """Quick summary of one dive: location, depth, duration, SAC."""
    dive_df = filter_dive(df, dive_number)
    if dive_df.empty:
        return f"No data found for dive number {dive_number}."
    if features is None:
        features = extract_features(df)
    row = _features_row(df, dive_number, features)
    if row is None:
        return f"Could not extract features for dive {dive_number}."

    return (
        f"Dive {dive_number}:\n"
        f"  Location: {_location(row)}\n"
        f"  Max Depth: {row['max_depth']:.1f}m\n"
        f"  Duration: {dive_df['time'].max() / 60:.0f} minutes\n"
        f"  SAC Rate: {fmt(row.get('sac_rate'), unit=' L/min')}\n"
        f"{coverage_line(features)}"
    )


def list_dives(df: pd.DataFrame, features: pd.DataFrame | None = None) -> str:
    """List all dives with site and max depth."""
    if df.empty:
        return "No dive data loaded."
    if features is None:
        features = extract_features(df)
    rows = sorted(
        (row for _, row in features.iterrows()),
        key=lambda r: dive_sort_key(r["dive_number"]),
    )
    lines = []
    for row in rows:
        lines.append(
            f"  #{row['dive_number']}: {_location(row)} — {row['max_depth']:.1f}m max"
        )
    return (
        f"Loaded dives ({len(rows)}):\n"
        + "\n".join(lines)
        + f"\n{coverage_line(features)}"
    )


def analyze_all_dives(df: pd.DataFrame, features: pd.DataFrame | None = None) -> str:
    """Whole-log analysis: aggregate stats, safety concerns, worst offenders.

    Each percentage is over the dives where that metric was recorded.
    """
    if df.empty:
        return "No dive data loaded."
    if features is None:
        features = extract_features(df)
    if features.empty:
        return "Could not extract features from dive data."

    n = len(features)
    cov = data_coverage(features)

    deepest = features.loc[features["max_depth"].idxmax()]
    stats_lines = [
        f"Total dives: {n}",
        f"Avg max depth: {features['max_depth'].mean():.1f}m",
        f"Deepest dive: #{deepest['dive_number']} at {deepest['max_depth']:.1f}m",
    ]
    if cov["sac"]:
        stats_lines.append(
            f"Avg SAC rate: {features['sac_rate'].mean():.1f} L/min "
            f"(over {cov['sac']} dives with SAC)"
        )

    def _pct(count: int, of: int, what: str = "dives") -> str:
        if of == 0:
            return "not recorded in any dive"
        return f"{count}/{of} {what} ({count * 100 // of}%)"

    ndl = features["min_ndl"]
    surfacing = features["max_shallow_ascend_speed"]
    concern_lines = [
        f"High sustained ascent rate (>{ASCENT_LIMIT_M_MIN:.0f} m/min over "
        f"{ASCENT_WINDOW_S:.0f} s): "
        f"{_pct(int((features['max_ascend_speed'] > ASCENT_LIMIT_M_MIN).sum()), n)}",
        f"Bolted to the surface (>{SHALLOW_ASCENT_LIMIT_M_MIN:.0f} m/min through "
        f"the last {SHALLOW_ZONE_M:.0f} m): "
        f"{_pct(int((surfacing > SHALLOW_ASCENT_LIMIT_M_MIN).sum()), n)}",
        *_deco_concerns(features, n, _pct),
        f"NDL under {NDL_DANGER_MIN:.0f} min (a fact, no limit broken): "
        f"{_pct(int((ndl < NDL_DANGER_MIN).sum()), cov['ndl'], 'dives with NDL')}",
    ]
    if "cold_minutes" in features.columns:
        temps = int(features["water_min_temp"].notna().sum())
        flags = features.apply(thermal_flags, axis=1, result_type="expand")
        concern_lines += [
            f"Prolonged cold (≥{PROLONGED_COLD_MIN:.0f} min below {COLD_WATER_C:.0f} °C): "
            f"{_pct(int(flags['prolonged_cold'].sum()), temps, 'dives with temperature')}",
            f"Cold ascent and stops (<{COLD_WATER_C:.0f} °C): "
            f"{_pct(int(flags['cold_stops'].sum()), temps, 'dives with temperature')}",
            f"Long warm-water dives (≥{LONG_WARM_MIN:.0f} min, water ≥{WARM_WATER_C:.0f} °C): "
            f"{_pct(int(flags['long_warm'].sum()), temps, 'dives with temperature')}",
        ]

    def _offenders(col: str) -> list[str]:
        return [
            f"  #{row['dive_number']} {_location(row)}: {row[col]:.1f} m/min"
            for _, row in features.nlargest(3, col).iterrows()
        ]

    return "\n".join(
        [
            "=== AGGREGATE DIVE ANALYSIS ===",
            "",
            "Overall Stats:",
            *[f"  {line}" for line in stats_lines],
            "",
            "Safety Concerns:",
            *[f"  {line}" for line in concern_lines],
            "",
            "What the log shows (share of the dives where each could be measured):",
            *[f"  {line}" for line in habit_lines(features)],
            "",
            f"Top Worst Offenders (sustained ascent, {ASCENT_WINDOW_S:.0f} s):",
            *_offenders("max_ascend_speed"),
            "",
            f"Top Worst Offenders (surfacing through the last {SHALLOW_ZONE_M:.0f} m):",
            *_offenders("max_shallow_ascend_speed"),
            "",
            coverage_line(features),
        ]
    )


def _deco_concerns(features: pd.DataFrame, n: int, pct) -> list[str]:
    if "technical" not in features.columns:
        return [f"Entered decompression: {pct(int(features['entered_deco'].sum()), n)}"]
    flags = features.apply(deco_flags, axis=1, result_type="expand")
    return [
        f"Technical dives (trimix, deco gas or rebreather): "
        f"{pct(int(flags['technical'].sum()), n)}",
        f"Entered decompression on a recreational dive: "
        f"{pct(int(flags['recreational_deco'].sum()), n)}",
        f"Missed a required deco stop: {pct(int(flags['missed_stop'].sum()), n)}",
        f"Surfaced with deco still owed: {pct(int(flags['surfaced_owing'].sum()), n)}",
    ]


FACT = "fact"
HABIT_HEADINGS = {
    LIMIT: "Limits broken",
    MARGIN: "Thin margins (the diver's call, not a limit broken)",
    FACT: "Facts, not faults",
}


def habit_counts(features: pd.DataFrame) -> list[tuple[str, str, int, int]]:
    """(class, habit, dives showing it, dives where it could be measured).

    Most common share first, habits no dive shows left out. Lets a reader
    tell what the log keeps repeating from what happened once, and a broken
    limit from a thin margin or a plain fact.
    """
    n = len(features)

    def col(name: str) -> pd.Series:
        return features[name] if name in features.columns else pd.Series(dtype=float)

    def count(mask) -> int:
        return int(mask.sum())

    deco = features.apply(deco_flags, axis=1, result_type="expand")
    thermal = features.apply(thermal_flags, axis=1, result_type="expand")
    profile = features.apply(profile_flags, axis=1, result_type="expand")
    gas = features.apply(gas_flags, axis=1, result_type="expand")
    recreational = ~deco["technical"]
    below_stop_depth = count(features["max_depth"] > STOP_EXPECTED_BELOW_M)
    low_ndl = (
        recreational & ~deco["recreational_deco"] & (col("min_ndl") < NDL_DANGER_MIN)
    )
    temps = count(col("water_min_temp").notna())
    po2_known = count(col("max_po2").notna())
    habits = [
        (
            LIMIT,
            "missed a deco stop or surfaced owing one",
            count(deco["missed_stop"] | deco["surfaced_owing"]),
            n,
        ),
        (
            LIMIT,
            f"oxygen pressure over the {PO2_WORKING_BAR:.1f} bar working limit",
            count(gas["po2_over_working"]),
            po2_known,
        ),
        (
            LIMIT,
            f"bolted to the surface (over {SHALLOW_ASCENT_LIMIT_M_MIN:.0f} m/min "
            f"from the safety stop)",
            count(col("max_shallow_ascend_speed") > SHALLOW_ASCENT_LIMIT_M_MIN),
            count(col("max_shallow_ascend_speed").notna()),
        ),
        (
            LIMIT,
            f"sustained ascent over {ASCENT_LIMIT_M_MIN:.0f} m/min",
            count(col("max_ascend_speed") > ASCENT_LIMIT_M_MIN),
            count(col("max_ascend_speed").notna()),
        ),
        (
            MARGIN,
            f"ended below {RESERVE_BAR:.0f} bar",
            count(profile["low_reserve"]),
            count(col("end_pressure").notna() & recreational),
        ),
        (
            MARGIN,
            f"safety stop under {SAFETY_STOP_MIN:.0f} min",
            count(profile["short_stop"]),
            count(col("safety_stop_minutes").notna()),
        ),
        (MARGIN, "sawtooth profile", count(profile["sawtooth"]), below_stop_depth),
        (
            MARGIN,
            "surfaced mid-dive",
            count(profile["surfaced_mid_dive"]),
            below_stop_depth,
        ),
        (
            MARGIN,
            f"gas density over the recommended {GAS_DENSITY_RECOMMENDED_G_L:.1f} g/L "
            f"at depth",
            count(gas["density_over_recommended"]),
            po2_known,
        ),
        (
            MARGIN,
            f"deeper than {RECREATIONAL_MAX_M:.0f} m",
            count((features["max_depth"] > RECREATIONAL_MAX_M) & recreational),
            n,
        ),
        (MARGIN, "prolonged cold", count(thermal["prolonged_cold"]), temps),
        (MARGIN, "cold stops", count(thermal["cold_stops"]), temps),
        (
            FACT,
            "went into deco on a recreational dive and kept the stops",
            count(deco["recreational_deco"] & deco["stops_kept"]),
            n,
        ),
        (
            FACT,
            f"NDL under {NDL_DANGER_MIN:.0f} min",
            count(low_ndl),
            count(col("min_ndl").notna()),
        ),
        (FACT, f"deeper than {DEEP_M:.0f} m", count(features["max_depth"] > DEEP_M), n),
        (
            FACT,
            f"SAC over {SAC_HIGH_L_MIN:.0f} L/min",
            count(col("sac_rate") > SAC_HIGH_L_MIN),
            count(col("sac_rate").notna()),
        ),
        (
            FACT,
            f"descent over {FAST_DESCENT_M_MIN:.0f} m/min",
            count(profile["fast_descent"]),
            count(col("max_descent_speed").notna()),
        ),
        (FACT, "long warm-water dive", count(thermal["long_warm"]), temps),
    ]
    shown = [h for h in habits if h[2] > 0 and h[3] > 0]
    return sorted(shown, key=lambda h: h[2] / h[3], reverse=True)


def _share(count: int, of: int) -> str:
    percent = count * 100 // of
    return f"{percent}%" if percent else "under 1%"


def habit_lines(features: pd.DataFrame) -> list[str]:
    """One line per class: what the log shows, most common first."""
    habits = habit_counts(features)
    lines = []
    for cls, heading in HABIT_HEADINGS.items():
        items = [
            f"{habit} {count}/{of} dives ({_share(count, of)})"
            for habit_cls, habit, count, of in habits
            if habit_cls == cls
        ]
        lines.append(f"{heading}: {'; '.join(items) if items else 'none'}")
    return lines


# DAN is searched once per phrase and each search adds material to the turn,
# so only the habits the log shows most are searched.
MAX_ANOMALY_QUERIES = 6


def anomaly_queries(features: pd.DataFrame | None) -> list[str]:
    """One DAN search phrase per anomaly actually measured in the log.

    The most common anomalies come first and at most ``MAX_ANOMALY_QUERIES``
    are kept, so the material follows what this log repeats. A missed or
    skipped deco stop always leads. Descriptive facts (depth on its own, air
    consumption) aren't anomalies. NaN never triggers a phrase.
    """
    if features is None or features.empty:
        return []

    index = features.index
    columns = {name: features[name] for name in features.columns}

    def col(name: str) -> pd.Series:
        return columns.get(name, pd.Series(float("nan"), index=index))

    has_deco = "technical" in features.columns
    deco = features.apply(deco_flags, axis=1, result_type="expand")
    thermal = features.apply(thermal_flags, axis=1, result_type="expand")
    profile = features.apply(profile_flags, axis=1, result_type="expand")
    gas = features.apply(gas_flags, axis=1, result_type="expand")
    always = pd.Series(True, index=features.index)
    never = pd.Series(False, index=features.index)
    missed = deco["missed_stop"] | deco["surfaced_owing"] if has_deco else never
    candidates = [
        # A skipped stop leads however rare it is.
        (
            always if missed.any() else never,
            "missed decompression stop omitted deco surfaced",
        ),
        (
            col("max_ascend_speed") > ASCENT_LIMIT_M_MIN,
            "rapid ascent decompression sickness",
        ),
        (
            col("max_shallow_ascend_speed") > SHALLOW_ASCENT_LIMIT_M_MIN,
            "fast final ascent to surface safety stop skipped",
        ),
        (
            profile["low_reserve"],
            "out of air low on gas running out of breathing gas",
        ),
        (profile["short_stop"], "omitted safety stop"),
        (
            profile["sawtooth"] | profile["surfaced_mid_dive"],
            "sawtooth profile repeated ascents yo-yo diving",
        ),
        (
            gas["po2_over_working"],
            "oxygen toxicity nitrox maximum operating depth exceeded",
        ),
        (
            thermal["prolonged_cold"],
            "hypothermia cold water exposure thermal protection",
        ),
        (
            thermal["cold_stops"],
            "cold during decompression thermal status DCS risk",
        ),
        (
            thermal["long_warm"],
            "dehydration warm water decompression sickness hydration",
        ),
    ]
    found = [(float(mask.mean()), phrase) for mask, phrase in candidates if mask.any()]
    found.sort(key=lambda item: item[0], reverse=True)  # stable: ties keep this order
    return [phrase for _, phrase in found[:MAX_ANOMALY_QUERIES]]


def build_anomaly_keywords(features: pd.DataFrame | None) -> str:
    """The anomaly phrases joined into one string, to enrich a search query."""
    return " ".join(anomaly_queries(features))
