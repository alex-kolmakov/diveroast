import numpy as np
import pandas as pd


def calculate_ascend_speed(data, shallow_threshold=2.0):
    """Calculate max ascend speed and count of high-speed ascent instances per dive.

    Samples shallower than ``shallow_threshold`` metres are excluded from
    speed calculations because near-surface depth sensor readings are noisy
    and produce unrealistically high ascent rates (e.g. 80 m/min from a 1 m
    depth change in 1 second on surfacing).
    """
    data["time_diff"] = data.groupby("dive_number")["time"].diff().fillna(0)
    data["depth_diff"] = data.groupby("dive_number")["depth"].diff().fillna(0)
    data["ascend_speed"] = (
        data["depth_diff"] / data["time_diff"]
    ) * 60  # Convert to meters per minute
    data["ascend_speed"] = (
        data["ascend_speed"] * -1
    )  # Because positive depth diff means we are gaining depth
    data["ascend_speed"] = (
        data["ascend_speed"].replace([np.inf, -np.inf], np.nan).fillna(0)
    )  # Handle infinite and NaN values

    # Zero out ascent speed for near-surface samples where sensor noise
    # causes unrealistically high readings.
    prev_depth = data["depth"] - data["depth_diff"]
    shallow_mask = (data["depth"] < shallow_threshold) | (
        prev_depth < shallow_threshold
    )
    data.loc[shallow_mask, "ascend_speed"] = 0

    # Calculate the maximum ascend speed per dive
    max_ascend_speed = data.groupby("dive_number")["ascend_speed"].max().reset_index()
    max_ascend_speed.rename(columns={"ascend_speed": "max_ascend_speed"}, inplace=True)

    # Calculate the count of high ascend speed instances per dive
    high_ascend_speed_count = (
        data[data["ascend_speed"] > 10]
        .groupby("dive_number")["ascend_speed"]
        .count()
        .reset_index()
    )
    high_ascend_speed_count.rename(
        columns={"ascend_speed": "high_ascend_speed_count"}, inplace=True
    )

    # Merge the two results
    ascend_speed_features = pd.merge(
        max_ascend_speed, high_ascend_speed_count, on="dive_number", how="left"
    )
    ascend_speed_features["high_ascend_speed_count"] = ascend_speed_features[
        "high_ascend_speed_count"
    ].fillna(0)

    return ascend_speed_features


def label_adverse_conditions(features_df):
    """Label dives with adverse conditions (rating < 3)."""
    features_df["adverse_conditions"] = (features_df["rating"] < 3).astype(int)
    return features_df


def extract_features(df: pd.DataFrame) -> pd.DataFrame:
    """Extract per-dive features from a raw per-sample DataFrame.

    Takes a DataFrame with per-sample dive data (as returned by parsers)
    and aggregates it into per-dive features.
    """
    data = df.copy()
    # Calculate ascend speed features
    ascend_speed_features = calculate_ascend_speed(data)

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
            sac_rate=("sac_rate", "first"),
            rating=("rating", "first"),
            **(
                {
                    "dive_site_name": ("dive_site_name", "first"),
                }
                if "dive_site_name" in data.columns
                else {}
            ),
            **(
                {
                    "trip_name": ("trip_name", "first"),
                }
                if "trip_name" in data.columns
                else {}
            ),
            **(
                {
                    "latitude": ("latitude", "first"),
                }
                if "latitude" in data.columns
                else {}
            ),
            **(
                {
                    "longitude": ("longitude", "first"),
                }
                if "longitude" in data.columns
                else {}
            ),
        )
        .reset_index()
    )

    # Merge the ascend speed features with other features
    features = features.merge(ascend_speed_features, on="dive_number")

    # Temperature gradient: difference between warmest (surface) and coldest (depth)
    # Captures the thermocline the diver crossed within the dive.
    features["temp_gradient"] = (features["max_temp"] - features["min_temp"]).clip(
        lower=0
    )
    # Fill NaN values in variability columns with the mean of each column
    features["depth_variability"] = features["depth_variability"].fillna(
        features["depth_variability"].mean()
    )
    features["temp_variability"] = features["temp_variability"].fillna(
        features["temp_variability"].mean()
    )
    features["pressure_variability"] = features["pressure_variability"].fillna(
        features["pressure_variability"].mean()
    )
    features["avg_pressure"] = features["avg_pressure"].fillna(
        features["avg_pressure"].mean()
    )
    features["max_pressure"] = features["max_pressure"].fillna(
        features["max_pressure"].mean()
    )
    features["sac_rate"] = features["sac_rate"].fillna(features["sac_rate"].mean())

    # Fill any remaining NaN values (e.g. single-dive with all-NaN pressure)
    # with 0 before dropping rows that are entirely empty.
    features = features.fillna(0)
    features = features.dropna()

    return label_adverse_conditions(features)
