"""UDDF (Universal Dive Data Format) -> per-sample DataFrame.

UDDF is the XML interchange format written by Subsurface, Diving Log,
MacDive, DiveMate and others. Values are SI: depth in metres, time in
seconds, temperature in Kelvin, pressure in Pascal. Both the namespaced
(UDDF 3.2, ``http://www.streit.cc/uddf/3.2/``) and bare variants are read.

Mapping to the Subsurface parser's columns:
    time         waypoint/divetime (s)
    depth        waypoint/depth (m)
    temperature  waypoint/temperature (K -> °C)
    pressure     waypoint/tankpressure (Pa -> bar), first tank in the file
    ndl          waypoint/nodecotime (s -> min)
    in_deco      1 when a waypoint's decostop is kind="mandatory" with
                 decodepth > 0; safety stops don't count
    dive_site_name, latitude, longitude  from the linked divesite/site
    trip_name    from divetrip/trip/.../relateddives links
Diver-entered fields (rating, visibility, notes) are ignored.
"""

import pandas as pd
from defusedxml import ElementTree as ET

from src.parsers.base import DiveLogParser

KELVIN = 273.15
PASCAL_PER_BAR = 100_000.0

COLUMNS = [
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
    "latitude",
    "longitude",
]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el, name: str) -> list:
    return [c for c in el if _local(c.tag) == name]


def _child(el, name: str):
    return next((c for c in el if _local(c.tag) == name), None)


def _iter(el, name: str):
    return (c for c in el.iter() if _local(c.tag) == name)


def _float(el) -> float | None:
    if el is None or el.text is None:
        return None
    try:
        return float(el.text.strip())
    except ValueError:
        return None


def _text(el) -> str | None:
    return el.text.strip() if el is not None and el.text and el.text.strip() else None


def is_uddf(root) -> bool:
    return _local(root.tag).lower() == "uddf"


def _sites(root) -> dict[str, dict]:
    sites = {}
    for site in _iter(root, "site"):
        geo = _child(site, "geography")
        sites[site.get("id", "")] = {
            "name": _text(_child(site, "name")) or "N/A",
            "latitude": _float(_child(geo, "latitude")) if geo is not None else None,
            "longitude": _float(_child(geo, "longitude")) if geo is not None else None,
        }
    return sites


def _trips(root) -> dict[str, str]:
    """dive id -> trip name."""
    trips = {}
    for trip in _iter(root, "trip"):
        name = _text(_child(trip, "name")) or "N/A"
        for link in _iter(trip, "link"):
            if link.get("ref"):
                trips[link.get("ref")] = name
    return trips


def _primary_tank(root) -> str | None:
    """The first tank referenced by any tankpressure reading."""
    first = next(_iter(root, "tankpressure"), None)
    return first.get("ref") if first is not None else None


def _dive_rows(dive, sites, trips, primary_tank, fallback_number) -> list[dict]:
    before = _child(dive, "informationbeforedive")
    number = _text(_child(before, "divenumber")) if before is not None else None
    if number is None:
        when = _text(_child(before, "datetime")) if before is not None else None
        number = (
            "unnum_" + when.replace("T", "_").replace(":", "")[:17]
            if when
            else fallback_number
        )
    site = {"name": "N/A", "latitude": None, "longitude": None}
    if before is not None:
        for link in _children(before, "link"):
            if link.get("ref") in sites:
                site = sites[link.get("ref")]
                break
    trip = trips.get(dive.get("id", ""), "N/A")

    rows = []
    samples = _child(dive, "samples")
    for wp in _children(samples, "waypoint") if samples is not None else []:
        time = _float(_child(wp, "divetime"))
        depth = _float(_child(wp, "depth"))
        if time is None or depth is None:
            continue
        temp_k = _float(_child(wp, "temperature"))
        pressures = _children(wp, "tankpressure")
        tank = next(
            (p for p in pressures if p.get("ref") == primary_tank),
            pressures[0] if pressures and primary_tank is None else None,
        )
        pressure_pa = _float(tank)
        ndl_s = _float(_child(wp, "nodecotime"))
        deco = any(
            stop.get("kind") == "mandatory" and float(stop.get("decodepth") or 0) > 0
            for stop in _children(wp, "decostop")
        )
        rows.append(
            {
                "dive_number": number,
                "trip_name": trip,
                "dive_site_name": site["name"],
                "time": time,
                "depth": depth,
                "temperature": temp_k - KELVIN if temp_k is not None else None,
                "pressure": pressure_pa / PASCAL_PER_BAR
                if pressure_pa is not None
                else None,
                "rbt": None,
                "ndl": ndl_s / 60 if ndl_s is not None else None,
                "in_deco": int(deco),
                "sac_rate": None,
                "latitude": site["latitude"],
                "longitude": site["longitude"],
            }
        )
    return rows


def parse_uddf_root(root) -> pd.DataFrame:
    sites, trips, primary_tank = _sites(root), _trips(root), _primary_tank(root)
    rows: list[dict] = []
    seen: dict[str, int] = {}
    for i, dive in enumerate(_iter(root, "dive"), start=1):
        dive_rows = _dive_rows(dive, sites, trips, primary_tank, f"unnum_{i}")
        if not dive_rows:
            continue
        # Two dives sharing a number would merge into one; keep them apart.
        number = dive_rows[0]["dive_number"]
        seen[number] = seen.get(number, 0) + 1
        if seen[number] > 1:
            for row in dive_rows:
                row["dive_number"] = f"{number}@{seen[number]}"
        rows.extend(dive_rows)
    frame = pd.DataFrame(rows, columns=COLUMNS)
    numeric = ["time", "depth", "temperature", "pressure", "rbt", "ndl", "in_deco"]
    numeric += ["sac_rate", "latitude", "longitude"]
    frame[numeric] = frame[numeric].astype(float)
    return frame


class UddfParser(DiveLogParser):
    def parse(self, file_path: str) -> pd.DataFrame:
        root = ET.parse(file_path).getroot()
        if not is_uddf(root):
            raise ValueError(f"{file_path} is not a UDDF file")
        return parse_uddf_root(root)

    def supported_extensions(self) -> list[str]:
        return [".uddf"]
