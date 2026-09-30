import numpy as np
import pandas as pd

# Ascent-rate metric parameters
ASCENT_WINDOW_S = 30.0  # rate is measured over this trailing window
ASCENT_LIMIT_M_MIN = 10.0  # recommended maximum ascent rate
SHALLOW_THRESHOLD_M = 2.0  # sensor noise dominates shallower than this
FINAL_ASCENT_WINDOW_S = 60.0  # exclude the last minute before surfacing...
FINAL_ASCENT_MAX_DEPTH_M = 6.0  # ...but only in the safety-stop band

# An NDL of 0 is only a real reading if the computer counted down to it.
NDL_COUNTDOWN_MAX_MIN = 5.0


def _dive_ascent_rates(times: np.ndarray, depths: np.ndarray) -> np.ndarray:
    """Windowed ascent rate (m/min, positive = ascending) for one dive.

    Each sample's rate is the depth change over the trailing
    ``ASCENT_WINDOW_S`` seconds, linearly interpolated between samples, so it
    doesn't depend on the computer's sample interval. Samples that are
    ineligible (first window, near-surface noise, the final 5 m -> surface
    move) are NaN.
    """
    rates = np.full(len(times), np.nan)
    if len(times) < 2:
        return rates

    window_start = times - ASCENT_WINDOW_S
    eligible = window_start >= times[0]
    depth_then = np.interp(window_start, times, depths)
    rates[eligible] = (depth_then[eligible] - depths[eligible]) / ASCENT_WINDOW_S * 60

    # Near-surface readings are noisy (1 m in 1 s reads as 60 m/min).
    rates[(depths < SHALLOW_THRESHOLD_M) | (depth_then < SHALLOW_THRESHOLD_M)] = np.nan

    # The move from the safety stop to the surface is not what the ascent-rate
    # limit is about. Exclude the last minute before final surfacing, but only
    # samples in the safety-stop band, so a direct ascent from depth still
    # registers on its deeper part.
    deep = np.nonzero(depths >= SHALLOW_THRESHOLD_M)[0]
    if len(deep):
        last_deep = deep[-1]
        surfaced_at = times[min(last_deep + 1, len(times) - 1)]
        final_leg = (times > surfaced_at - FINAL_ASCENT_WINDOW_S) & (
            depths < FINAL_ASCENT_MAX_DEPTH_M
        )
        rates[final_leg] = np.nan
    return rates


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


def calculate_ascend_speed(data: pd.DataFrame) -> pd.DataFrame:
    """Max windowed ascent rate and number of fast-ascent events per dive.

    ``max_ascend_speed`` is the highest ``ASCENT_WINDOW_S``-second average
    ascent rate. ``high_ascend_speed_count`` is the number of separate
    episodes above ``ASCENT_LIMIT_M_MIN``, not the number of samples, so it
    doesn't scale with the computer's sample rate.
    """
    data = data.sort_values(["dive_number", "time"])
    rows = []
    for dive_number, dive in data.groupby("dive_number", sort=False):
        rates = _dive_ascent_rates(
            dive["time"].to_numpy(dtype=float), dive["depth"].to_numpy(dtype=float)
        )
        measured = rates[~np.isnan(rates)]
        over = np.nan_to_num(rates, nan=0.0) > ASCENT_LIMIT_M_MIN
        rows.append(
            {
                "dive_number": dive_number,
                "max_ascend_speed": float(max(measured.max(), 0.0))
                if len(measured)
                else 0.0,
                "high_ascend_speed_count": _count_events(over),
            }
        )
    return pd.DataFrame(
        rows, columns=["dive_number", "max_ascend_speed", "high_ascend_speed_count"]
    )


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
