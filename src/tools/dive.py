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
    SHALLOW_ASCENT_LIMIT_M_MIN,
    SHALLOW_ZONE_M,
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
    if row.get("entered_deco"):
        parts.append("entered decompression")
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
            f"(8 m to the surface is 1.8 to 1.0 bar), so this is where a fast ascent "
            f"matters most."
        )
    if row.get("entered_deco"):
        issues.append(
            "ENTERED DECOMPRESSION: the dive computer flagged a mandatory "
            "decompression obligation on this dive."
        )
    elif measured(row.get("min_ndl")) and row["min_ndl"] < NDL_DANGER_MIN:
        issues.append(
            f"DANGEROUSLY LOW NDL: Minimum NDL dropped to {row['min_ndl']:.0f} minutes. "
            f"This is cutting it extremely close to mandatory decompression."
        )
    if measured(row.get("sac_rate")) and row["sac_rate"] > SAC_HIGH_L_MIN:
        issues.append(
            f"HIGH AIR CONSUMPTION: SAC rate of {row['sac_rate']:.1f} L/min is above "
            f"{SAC_HIGH_L_MIN:.0f} L/min. Consider working on breathing technique and buoyancy."
        )
    if row.get("max_depth", 0) > DEEP_M:
        issues.append(
            f"DEEP DIVE: Maximum depth of {row['max_depth']:.1f}m. "
            f"Ensure you have appropriate training and gas planning for this depth."
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
    sac = features["sac_rate"]
    surfacing = features["max_shallow_ascend_speed"]
    concern_lines = [
        f"High sustained ascent rate (>{ASCENT_LIMIT_M_MIN:.0f} m/min over "
        f"{ASCENT_WINDOW_S:.0f} s): "
        f"{_pct(int((features['max_ascend_speed'] > ASCENT_LIMIT_M_MIN).sum()), n)}",
        f"Bolted to the surface (>{SHALLOW_ASCENT_LIMIT_M_MIN:.0f} m/min through "
        f"the last {SHALLOW_ZONE_M:.0f} m): "
        f"{_pct(int((surfacing > SHALLOW_ASCENT_LIMIT_M_MIN).sum()), n)}",
        f"Entered decompression: {_pct(int(features['entered_deco'].sum()), n)}",
        f"Low NDL (<{NDL_DANGER_MIN:.0f} min): "
        f"{_pct(int((ndl < NDL_DANGER_MIN).sum()), cov['ndl'], 'dives with NDL')}",
        f"High SAC (>{SAC_HIGH_L_MIN:.0f} L/min): "
        f"{_pct(int((sac > SAC_HIGH_L_MIN).sum()), cov['sac'], 'dives with SAC')}",
        f"Deep dives (>{DEEP_M:.0f}m): {_pct(int((features['max_depth'] > DEEP_M).sum()), n)}",
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
            f"Top Worst Offenders (sustained ascent, {ASCENT_WINDOW_S:.0f} s):",
            *_offenders("max_ascend_speed"),
            "",
            f"Top Worst Offenders (surfacing through the last {SHALLOW_ZONE_M:.0f} m):",
            *_offenders("max_shallow_ascend_speed"),
            "",
            coverage_line(features),
        ]
    )


def anomaly_queries(features: pd.DataFrame | None) -> list[str]:
    """One DAN search phrase per anomaly actually measured in the log.

    NaN never triggers a phrase.
    """
    if features is None or features.empty:
        return []
    keywords: list[str] = []
    if features["max_ascend_speed"].max() > ASCENT_LIMIT_M_MIN:
        keywords.append("rapid ascent decompression sickness")
    shallow = features.get("max_shallow_ascend_speed")
    if shallow is not None and shallow.max() > SHALLOW_ASCENT_LIMIT_M_MIN:
        keywords.append("fast final ascent to surface safety stop skipped")
    entered_deco = "entered_deco" in features.columns and features["entered_deco"].any()
    if entered_deco or (features["min_ndl"] < 1).any():
        keywords.append("close to deco stop NDL almost zero recreational limit")
    if (features["sac_rate"] > SAC_HIGH_L_MIN).any():
        keywords.append("high air consumption SAC rate breathing")
    if features["max_depth"].max() > DEEP_M:
        keywords.append("deep diving incident")
    if "cold_minutes" in features.columns:
        flags = features.apply(thermal_flags, axis=1, result_type="expand")
        if flags["prolonged_cold"].any():
            keywords.append("hypothermia cold water exposure thermal protection")
        if flags["cold_stops"].any():
            keywords.append("cold during decompression thermal status DCS risk")
        if flags["long_warm"].any():
            keywords.append("dehydration warm water decompression sickness hydration")
    return keywords


def build_anomaly_keywords(features: pd.DataFrame | None) -> str:
    """The anomaly phrases joined into one string, to enrich a search query."""
    return " ".join(anomaly_queries(features))
