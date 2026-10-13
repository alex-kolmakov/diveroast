"""Oxygen partial pressure and gas density at the deepest point of a dive."""

import numpy as np
import pytest

from src.analysis.feature_engineering import (
    GAS_DENSITY_MAX_G_L,
    GAS_DENSITY_RECOMMENDED_G_L,
    PO2_LIMIT_BAR,
    PO2_WORKING_BAR,
    gas_at_depth,
)


def _gas(depth: float, o2: float | None = 21.0, **extra) -> dict[str, float]:
    return gas_at_depth({"max_depth": depth, "min_o2": o2, **extra})


def test_air_at_40m_is_well_inside_the_oxygen_limit():
    assert _gas(40.0)["max_po2"] == pytest.approx(1.05)


def test_nitrox_is_judged_at_its_own_depth_limit():
    # EAN32: 1.4 bar at 33.75 m, 1.6 bar at 40 m.
    assert _gas(33.0, 32.0)["max_po2"] < PO2_WORKING_BAR
    assert PO2_WORKING_BAR < _gas(36.0, 32.0)["max_po2"] < PO2_LIMIT_BAR
    assert _gas(41.0, 32.0)["max_po2"] > PO2_LIMIT_BAR


def test_air_crosses_the_density_marks_near_30_and_38m():
    assert _gas(30.0)["gas_density"] < GAS_DENSITY_RECOMMENDED_G_L
    assert GAS_DENSITY_RECOMMENDED_G_L < _gas(34.0)["gas_density"] < GAS_DENSITY_MAX_G_L
    assert _gas(40.0)["gas_density"] > GAS_DENSITY_MAX_G_L


def test_helium_thins_the_gas():
    trimix = _gas(60.0, 18.0, max_helium=45.0)["gas_density"]
    assert trimix < _gas(60.0, 18.0)["gas_density"]
    assert trimix < GAS_DENSITY_MAX_G_L < _gas(60.0, 18.0)["gas_density"]


def test_unknown_gas_is_not_judged():
    assert np.isnan(_gas(40.0, None)["max_po2"])
    assert np.isnan(_gas(40.0, float("nan"))["gas_density"])


def test_rebreather_sets_its_own_oxygen_pressure():
    loop = _gas(40.0, 21.0, dive_mode="CCR")
    assert np.isnan(loop["max_po2"])
    assert np.isnan(loop["gas_density"])
