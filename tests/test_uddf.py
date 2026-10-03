"""UDDF (Universal Dive Data Format) parsing."""

import shutil
from pathlib import Path

import pytest

from src.analysis.feature_engineering import extract_features
from src.parsers import get_parser

UDDF = "tests/fixtures/sample.uddf"


@pytest.fixture(scope="module")
def df():
    return get_parser(UDDF).parse(UDDF)


def _dive(df, number):
    return df[df["dive_number"] == number]


def test_dives_sites_and_trips(df):
    assert sorted(df["dive_number"].unique()) == ["101", "unnum_2026-05-01_133000"]
    assert set(df["dive_site_name"]) == {"Test Reef"}
    assert set(df["trip_name"]) == {"Red Sea test week"}
    assert (df["latitude"].iloc[0], df["longitude"].iloc[0]) == (27.2579, 33.8116)


def test_si_units_are_converted(df):
    deep = _dive(df, "101")
    assert deep["temperature"].round(2).tolist()[:2] == [26.0, 24.0]  # K -> °C
    assert deep["pressure"].tolist()[:2] == [200.0, 185.0]  # Pa -> bar, main tank
    assert deep["ndl"].tolist()[:3] == [99.0, 8.0, 0.0]  # s -> min


def test_only_mandatory_stops_count_as_deco(df):
    features = extract_features(df).set_index("dive_number")
    assert bool(features.loc["101", "entered_deco"])
    assert not bool(features.loc["unnum_2026-05-01_133000", "entered_deco"])


def test_unrecorded_metrics_stay_missing(df):
    shallow = _dive(df, "unnum_2026-05-01_133000")
    assert shallow["temperature"].isna().all()
    assert shallow["pressure"].isna().all()
    assert shallow["ndl"].isna().all()
    assert df["sac_rate"].isna().all()


def test_uddf_saved_as_xml_is_detected(tmp_path):
    xml = tmp_path / "export.xml"
    shutil.copy(UDDF, xml)
    assert get_parser(str(xml)).parse(str(xml))["dive_number"].nunique() == 2


def test_duplicate_dive_numbers_are_kept_apart(tmp_path):
    text = (
        Path(UDDF)
        .read_text()
        .replace(
            "<datetime>2026-05-01T13:30:00</datetime>",
            "<divenumber>101</divenumber><datetime>2026-05-01T13:30:00</datetime>",
        )
    )
    path = tmp_path / "dupes.uddf"
    path.write_text(text)
    numbers = set(get_parser(str(path)).parse(str(path))["dive_number"])
    assert numbers == {"101", "101@2"}


def test_non_uddf_xml_is_rejected(tmp_path):
    path = tmp_path / "other.uddf"
    path.write_text("<divelog/>")
    with pytest.raises(ValueError, match="not a UDDF file"):
        get_parser(str(path)).parse(str(path))
