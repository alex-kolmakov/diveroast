import numpy as np
import pandas as pd

# Ascent-rate metrics come in two tiers:
#   sustained  - 30 s average over the whole dive (the 9-10 m/min guideline)
#   surfacing  - speed of each final approach to the surface through the last
#                8 m, where the relative pressure drop is largest (8 m ->
#                surface is 1.8 -> 1.0 bar, a 44% drop). Short bolts from the
#                safety stop are caught instead of being averaged away.
ASCENT_WINDOW_S = 30.0
ASCENT_LIMIT_M_MIN = 10.0
SHALLOW_THRESHOLD_M = 2.0  # sustained tier: sensor noise dominates shallower
SHALLOW_ZONE_M = 8.0
SURFACE_M = 1.0  # shallower than this counts as at the surface
# Depth marks the surfacing approach is timed from. The fastest wins, so a
# stop at 5 m followed by a sprint from 4 m is timed from 4 m.
SURFACING_MARKS_M = (SHALLOW_ZONE_M, 6.0, 5.0, 4.0, 3.0)
SHALLOW_ASCENT_LIMIT_M_MIN = 10.0

# Thermal exposure. Logs record water temperature, not body temperature or
# the suit, so these measure exposure, not the diver's state. DAN: prolonged
# cold-water exposure risks hypothermia; being cold during decompression (the
# ascent and stops) slows inert gas washout and raises DCS risk; warm,
# dehydrating conditions are a DCS factor too.
IN_WATER_M = 1.5  # shallower readings are often air or sun on the sensor
COLD_WATER_C = 10.0
STOP_ZONE_M = 6.0  # the ascent and safety-stop phase after the deepest point

# An NDL of 0 is only a real reading if the computer counted down to it.
NDL_COUNTDOWN_MAX_MIN = 5.0


def _window_rates(
    times: np.ndarray, depths: np.ndarray, window_s: float
) -> tuple[np.ndarray, np.ndarray]:
    """Ascent rate (m/min, positive = ascending) over a trailing time window.

    Depth at the window start is linearly interpolated between samples, so the
    rate doesn't depend on the computer's sample interval. Returns the rates
    (NaN for the first window of the dive) and the depth at each window start.
    """
    rates = np.full(len(times), np.nan)
    window_start = times - window_s
    depth_then = np.interp(window_start, times, depths)
    eligible = window_start >= times[0]
    rates[eligible] = (depth_then[eligible] - depths[eligible]) / window_s * 60
    return rates, depth_then


def _sustained_rates(times: np.ndarray, depths: np.ndarray) -> np.ndarray:
    """30 s ascent rates, excluding windows that touch the noisy top 2 m."""
    rates, depth_then = _window_rates(times, depths, ASCENT_WINDOW_S)
    rates[(depths < SHALLOW_THRESHOLD_M) | (depth_then < SHALLOW_THRESHOLD_M)] = np.nan
    return rates


def _crossing_time(
    times: np.ndarray, depths: np.ndarray, j: int, level: float
) -> float:
    """Time between samples j and j+1 at which depth crosses ``level``."""
    d0, d1 = depths[j], depths[j + 1]
    if d0 == d1:
        return float(times[j])
    return float(times[j] + (d0 - level) / (d0 - d1) * (times[j + 1] - times[j]))


def _surfacings(
    times: np.ndarray, depths: np.ndarray
) -> list[tuple[float, float, float]]:
    """(speed m/min, start s, end s) of each arrival at the surface.

    For every arrival shallower than ``SURFACE_M``, time the ascent from the
    last crossing of each mark in ``SURFACING_MARKS_M`` to the surface and
    keep the fastest; start is when that mark was crossed. Crossing times are
    interpolated between samples, so the result doesn't depend on the sample
    interval. Lingering shallow before surfacing makes the approach slow, not
    fast, so reef swimming in the top few metres isn't mistaken for a bolt.
    """
    at_surface = depths < SURFACE_M
    arrivals = np.nonzero(at_surface[1:] & ~at_surface[:-1])[0] + 1
    result = []
    for i in arrivals:
        surfaced_at = _crossing_time(times, depths, i - 1, SURFACE_M)
        fastest, started = 0.0, surfaced_at
        for mark in SURFACING_MARKS_M:
            deeper = np.nonzero(depths[:i] >= mark)[0]
            if not len(deeper):
                continue
            crossed = _crossing_time(times, depths, deeper[-1], mark)
            elapsed = surfaced_at - crossed
            if elapsed > 0 and (mark - SURFACE_M) / elapsed * 60 > fastest:
                fastest, started = (mark - SURFACE_M) / elapsed * 60, crossed
        result.append((fastest, started, surfaced_at))
    return result


def _surfacing_rates(times: np.ndarray, depths: np.ndarray) -> np.ndarray:
    """Approach speed (m/min) of each arrival at the surface that could be timed.

    An arrival from shallower than the lowest mark has nothing to time it
    from and is left out.
    """
    rates = [rate for rate, _, _ in _surfacings(times, depths) if rate > 0]
    return np.array(rates, dtype=float)


def ascent_events(times: np.ndarray, depths: np.ndarray) -> list[dict]:
    """Where on one dive's profile each fast ascent happened.

    Returns dicts with ``kind`` ("sustained" or "surfacing"), ``start`` and
    ``end`` (s) and the peak ``rate`` (m/min). A sustained event spans its
    over-limit run plus the averaging window that led into it.
    """
    order = np.argsort(times)
    times, depths = times[order], depths[order]
    events = []
    rates = _sustained_rates(times, depths)
    over = np.nan_to_num(rates, nan=0.0) > ASCENT_LIMIT_M_MIN
    i = 0
    while i < len(over):
        if not over[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(over) and over[j + 1]:
            j += 1
        events.append(
            {
                "kind": "sustained",
                "start": float(max(times[i] - ASCENT_WINDOW_S, times[0])),
                "end": float(times[j]),
                "rate": float(np.nanmax(rates[i : j + 1])),
            }
        )
        i = j + 1
    for rate, start, end in _surfacings(times, depths):
        if rate > SHALLOW_ASCENT_LIMIT_M_MIN:
            events.append(
                {"kind": "surfacing", "start": start, "end": end, "rate": rate}
            )
    return sorted(events, key=lambda e: e["start"])


def _peak_and_episodes(rates: np.ndarray, limit: float) -> tuple[float, int]:
    """Highest rate (NaN if none could be measured) and the runs above limit."""
    measured = rates[~np.isnan(rates)]
    peak = float(max(measured.max(), 0.0)) if len(measured) else float("nan")
    return peak, _count_events(np.nan_to_num(rates, nan=0.0) > limit)


def _count_events(over: np.ndarray) -> int:
    """Count contiguous runs of True (one run = one fast-ascent event)."""
    if not len(over):
        return 0
    starts = over & ~np.concatenate(([False], over[:-1]))
    return int(starts.sum())


def clean_ndl(data: pd.DataFrame) -> pd.Series:
    """Per-sample NDL with sentinel zeros removed.

    Some computers write ``ndl=0`` to mean "not computed" rather than "no
    time left": Suunto D5 exports jump 100 -> 0 -> 100 at the surface and
    mid-dive. A zero is kept only when the previous NDL reading in the same
    dive was at most ``NDL_COUNTDOWN_MAX_MIN``, i.e. the computer counted
    down to it. Expects ``data`` sorted by dive and time.
    """
    ndl = data["ndl"]
    previous = data.groupby("dive_number")["ndl"].transform(
        lambda s: s.ffill().shift(1)
    )
    sentinel = (ndl == 0) & ~(previous <= NDL_COUNTDOWN_MAX_MIN)
    return ndl.mask(sentinel)


ASCENT_COLUMNS = [
    "dive_number",
    "max_ascend_speed",
    "high_ascend_speed_count",
    "max_shallow_ascend_speed",
    "shallow_bolt_count",
]


def calculate_ascend_speed(data: pd.DataFrame) -> pd.DataFrame:
    """Two-tier ascent metrics per dive.

    Sustained: ``max_ascend_speed`` is the highest ``ASCENT_WINDOW_S``-second
    average ascent rate; ``high_ascend_speed_count`` is the number of separate
    episodes above ``ASCENT_LIMIT_M_MIN``.

    Surfacing: ``max_shallow_ascend_speed`` is the fastest approach to the
    surface through the last ``SHALLOW_ZONE_M``; ``shallow_bolt_count`` is
    the number of surfacings faster than ``SHALLOW_ASCENT_LIMIT_M_MIN``.

    Counts are episodes or surfacings, not samples, so they don't scale with
    the computer's sample rate. A rate that couldn't be measured is NaN, not
    0: the sustained tier needs a 30 s window deeper than
    ``SHALLOW_THRESHOLD_M``, the surfacing tier a crossing of one of
    ``SURFACING_MARKS_M``, so a very shallow dive has neither.
    """
    data = data.sort_values(["dive_number", "time"])
    rows = []
    for dive_number, dive in data.groupby("dive_number", sort=False):
        times = dive["time"].to_numpy(dtype=float)
        depths = dive["depth"].to_numpy(dtype=float)
        sustained_peak, sustained_n = _peak_and_episodes(
            _sustained_rates(times, depths), ASCENT_LIMIT_M_MIN
        )
        surfacings = _surfacing_rates(times, depths)
        shallow_peak = float(surfacings.max()) if len(surfacings) else float("nan")
        shallow_n = int((surfacings > SHALLOW_ASCENT_LIMIT_M_MIN).sum())
        rows.append(
            {
                "dive_number": dive_number,
                "max_ascend_speed": sustained_peak,
                "high_ascend_speed_count": sustained_n,
                "max_shallow_ascend_speed": shallow_peak,
                "shallow_bolt_count": shallow_n,
            }
        )
    return pd.DataFrame(rows, columns=ASCENT_COLUMNS)


def _thermal(dive: pd.DataFrame) -> dict[str, float]:
    """Exposure features for one dive (sorted by time); NaN if not recorded.

    Computers log temperature sparsely (Subsurface only on change), so each
    reading holds until the next. Only in-water time counts. The first
    readings are often air or sun on the sensor, which then cools with a
    lag, so "warm water" is judged from the coldest reading: lag can't
    inflate it.
    """
    times = dive["time"].to_numpy(dtype=float)
    out = {
        "dive_minutes": (times.max() - times.min()) / 60 if len(times) > 1 else np.nan,
        "water_min_temp": np.nan,
        "cold_minutes": np.nan,
        "stop_temp": np.nan,
    }
    # Carry readings forward over the whole dive first: a computer that logs
    # only on change may have one reading, taken in the first metre. Then
    # measure only the in-water part.
    in_water = dive["depth"] >= IN_WATER_M
    water = dive[in_water]
    temps = dive["temperature"].ffill()[in_water]
    known = temps.notna()
    if not known.any():
        return out
    t = water["time"].to_numpy(dtype=float)
    step = np.diff(t, append=t[-1])  # each sample holds until the next one
    out["water_min_temp"] = float(temps[known].min())
    cold = (temps < COLD_WATER_C).to_numpy() & known.to_numpy()
    out["cold_minutes"] = float(step[cold].sum() / 60)
    deepest = dive["time"].iloc[int(np.argmax(dive["depth"].to_numpy()))]
    stops = known & (water["time"] > deepest) & (water["depth"] <= STOP_ZONE_M)
    if stops.any():
        out["stop_temp"] = float(temps[stops].mean())
    return out


def thermal_exposure(data: pd.DataFrame) -> pd.DataFrame:
    """Per-dive thermal exposure, from data sorted by dive and time."""
    rows = [
        {"dive_number": number, **_thermal(dive)}
        for number, dive in data.groupby("dive_number", sort=False)
    ]
    return pd.DataFrame(rows)


def extract_features(df: pd.DataFrame) -> pd.DataFrame:
    """Extract per-dive features from a raw per-sample DataFrame.

    Metrics the log didn't record stay NaN: no mean fills and no zero fills.
    A dive without NDL readings has ``min_ndl`` NaN, a dive without a tank
    pod has NaN pressure fields. Callers must treat NaN as "not recorded".

    Only signals any dive computer can produce are used: no star ratings or
    other diver-entered fields.
    """
    data = df.sort_values(["dive_number", "time"])
    if "in_deco" not in data.columns:
        data = data.assign(in_deco=np.nan)
    data = data.assign(ndl=clean_ndl(data))
    ascend_speed_features = calculate_ascend_speed(data)

    optional = {
        col: (col, "first")
        for col in ("dive_site_name", "trip_name", "latitude", "longitude")
        if col in data.columns
    }
    features = (
        data.groupby("dive_number")
        .agg(
            avg_depth=("depth", "mean"),
            max_depth=("depth", "max"),
            depth_variability=("depth", "std"),
            avg_temp=("temperature", "mean"),
            max_temp=("temperature", "max"),
            min_temp=("temperature", "min"),
            temp_variability=("temperature", "std"),
            avg_pressure=("pressure", "mean"),
            max_pressure=("pressure", "max"),
            pressure_variability=("pressure", "std"),
            min_ndl=("ndl", "min"),
            deco_flag=("in_deco", "max"),
            sac_rate=("sac_rate", "first"),
            **optional,
        )
        .reset_index()
    )

    # A real deco entry, from the computer's own flag. Kept separate from
    # min_ndl so that 0 NDL and "no NDL recorded" are never the same value.
    features["entered_deco"] = features.pop("deco_flag").fillna(0) >= 1

    features = features.merge(ascend_speed_features, on="dive_number")
    features = features.merge(thermal_exposure(data), on="dive_number")

    # Temperature gradient: difference between warmest (surface) and coldest (depth)
    # Captures the thermocline the diver crossed within the dive.
    features["temp_gradient"] = (features["max_temp"] - features["min_temp"]).clip(
        lower=0
    )

    return features


def data_coverage(features: pd.DataFrame) -> dict[str, int]:
    """Count how many dives actually recorded each safety metric."""
    return {
        "dives": len(features),
        "sac": int(features["sac_rate"].notna().sum()),
        "ndl": int(features["min_ndl"].notna().sum()),
        "pressure": int(features["avg_pressure"].notna().sum()),
    }
