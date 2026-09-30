import xml.etree.ElementTree as ET

import pytest

from src.parsers import PARSER_REGISTRY, get_parser
from src.parsers.subsurface import extract_all_dive_profiles_refined, time_to_seconds

FIXTURE_PATH = "tests/fixtures/anonymized_subsurface_export.ssrf"


def test_time_to_seconds():
    assert time_to_seconds("1:30") == 90
    assert time_to_seconds("0:45") == 45
    assert time_to_seconds("2:00") == 120
    assert time_to_seconds("60") == 60.0
    assert time_to_seconds("90") == 90.0


def test_extract_all_dive_profiles_refined():
    with open(FIXTURE_PATH) as file:
        tree = ET.parse(file)
        root = tree.getroot()

    df = extract_all_dive_profiles_refined(root)

    assert not df.empty, "The dataframe is empty"
    assert set(df.columns) == {
        "dive_number",
        "trip_name",
        "dive_site_name",
        "time",
        "depth",
        "temperature",
        "pressure",
        "rbt",
        "ndl",
        "in_deco",
        "sac_rate",
        "rating",
        "latitude",
        "longitude",
    }
    assert len(df) == 37882, "Dataframe should have 37882 rows"
    assert df["depth"].iloc[200] == 4.7
    assert df["rating"].iloc[200] == 4
    assert df["sac_rate"].iloc[200] == 41.418
    assert df["time"].iloc[200] == 720


def test_get_parser_ssrf():
    parser = get_parser("dive_export.ssrf")
    assert parser.__class__.__name__ == "SubsurfaceParser"


def test_get_parser_xml():
    parser = get_parser("dive_export.xml")
    assert parser.__class__.__name__ == "SubsurfaceParser"


def test_get_parser_unsupported():
    with pytest.raises(ValueError, match="Unsupported file type"):
        get_parser("dive_export.csv")


def test_parser_registry_keys():
    assert ".ssrf" in PARSER_REGISTRY
    assert ".xml" in PARSER_REGISTRY


def test_subsurface_parser_parse():
    parser = get_parser(FIXTURE_PATH)
    df = parser.parse(FIXTURE_PATH)
    assert not df.empty
    assert len(df) == 37882
