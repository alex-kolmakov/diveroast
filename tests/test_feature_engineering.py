import xml.etree.ElementTree as ET

import pandas as pd

from src.analysis.feature_engineering import calculate_ascend_speed, extract_features
from src.parsers.subsurface import extract_all_dive_profiles_refined

FIXTURE_PATH = "tests/fixtures/anonymized_subsurface_export.ssrf"


def test_calculate_ascend_speed():
    # Sample data setup
    data = pd.DataFrame(
        {
            "dive_number": [1, 1, 1, 2, 2],
            "time": [0, 1, 2, 0, 1],
            "depth": [20, 15, 10, 30, 20],
        }
    )

    # Expected results setup
    expected_max_ascend_speed = pd.DataFrame(
        {"dive_number": [1, 2], "max_ascend_speed": [300.0, 600.0]}
    )

    expected_high_ascend_speed_count = pd.DataFrame(
        {"dive_number": [1, 2], "high_ascend_speed_count": [2, 1]}
    )

    expected_output = pd.merge(
        expected_max_ascend_speed,
        expected_high_ascend_speed_count,
        on="dive_number",
        how="left",
    )
    expected_output["high_ascend_speed_count"] = expected_output[
        "high_ascend_speed_count"
    ].fillna(0)

    # Running the function
    result = calculate_ascend_speed(data)

    # Assertions
    pd.testing.assert_frame_equal(result, expected_output)


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
        "sac_rate",
        "rating",
        "max_ascend_speed",
        "high_ascend_speed_count",
        "adverse_conditions",
        "dive_site_name",
        "trip_name",
        "latitude",
        "longitude",
    }
    assert set(features.columns) == expected_columns
    assert features["adverse_conditions"].isin([0, 1]).all()
