"""Garmin FIT dive activities (Descent series) -> per-sample DataFrame.

Decoded with fitdecode (MIT). Garmin's own Python SDK is not used: its
licence restricts use to "internal business purposes".

A FIT file holds one activity. Only scuba sub-sports are accepted: freedives
(apnea) follow different physiology, and non-dive activities have no depth.

Mapping to the Subsurface parser's columns:
    time         seconds since the first record
    depth        record.depth (m)
    temperature  record.temperature (°C)
    pressure     tank_update.pressure (bar), carried to the following records
    ndl          record.ndl_time (s) / 60, in minutes like Subsurface
    in_deco      1 while record.next_stop_depth > 0 (a deco stop is owed;
                 Garmin doesn't report safety stops here), else 0
    sac_rate     dive_summary.avg_volume_sac (L/min), when air-integrated
    latitude/longitude  session start position (semicircles -> degrees)
FIT has no dive site or trip names, so those are "N/A".
"""

from datetime import datetime

import fitdecode
import pandas as pd

from src.parsers.base import DiveLogParser

SCUBA_SUB_SPORTS = {
    "single_gas_diving",
    "multi_gas_diving",
    "gauge_diving",
    "ccr_diving",
}
SEMICIRCLE_TO_DEG = 180.0 / 2**31

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


class NotAScubaDiveError(ValueError):
    """The FIT file is a valid activity, but not a scuba dive."""


def read_messages(file_path: str) -> dict[str, list[dict]]:
    """Decode a FIT file into {message name: [field dicts]}."""
    messages: dict[str, list[dict]] = {}
    with fitdecode.FitReader(file_path) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                messages.setdefault(frame.name, []).append(
                    {f.name: f.value for f in frame.fields}
                )
    return messages


def _degrees(semicircles) -> float | None:
    return semicircles * SEMICIRCLE_TO_DEG if semicircles is not None else None


def messages_to_frame(
    messages: dict[str, list[dict]], source: str = ""
) -> pd.DataFrame:
    """Convert decoded FIT messages for one dive into per-sample rows."""
    sport = (messages.get("sport") or [{}])[0]
    session = (messages.get("session") or [{}])[0]
    sub_sport = sport.get("sub_sport") or session.get("sub_sport")
    if sub_sport not in SCUBA_SUB_SPORTS:
        kind = sub_sport or sport.get("sport") or "unknown"
        raise NotAScubaDiveError(
            f"{source or 'FIT file'} is not a scuba dive (activity type: {kind})"
        )

    records = [
        r
        for r in messages.get("record", [])
        if r.get("timestamp") is not None and r.get("depth") is not None
    ]
    if not records:
        raise NotAScubaDiveError(f"{source or 'FIT file'} has no depth samples")

    summaries = messages.get("dive_summary", [])
    dive_summary = next(
        (s for s in summaries if s.get("reference_mesg") == "session"),
        summaries[-1] if summaries else {},
    )

    start: datetime = records[0]["timestamp"]
    local = (messages.get("activity") or [{}])[0].get("local_timestamp")
    started = local if isinstance(local, datetime) else start
    number = dive_summary.get("dive_number")
    dive_number = (
        str(number)
        if number is not None
        else f"unnum_{started:%Y-%m-%d}_{started:%H%M%S}"
    )

    lat = _degrees(session.get("start_position_lat"))
    lon = _degrees(session.get("start_position_long"))
    if lat is None or lon is None:
        located = next((r for r in records if r.get("position_lat") is not None), {})
        lat = _degrees(located.get("position_lat"))
        lon = _degrees(located.get("position_long"))

    frame = pd.DataFrame(
        {
            "timestamp": [r["timestamp"] for r in records],
            "time": [(r["timestamp"] - start).total_seconds() for r in records],
            "depth": [float(r["depth"]) for r in records],
            "temperature": [r.get("temperature") for r in records],
            "ndl": [
                r["ndl_time"] / 60 if r.get("ndl_time") is not None else None
                for r in records
            ],
            "in_deco": [int((r.get("next_stop_depth") or 0) > 0) for r in records],
        }
    )

    # Tank pressure arrives as separate tank_update messages; attach each
    # reading to the records that follow it, up to a minute later.
    updates = [
        u
        for u in messages.get("tank_update", [])
        if u.get("timestamp") is not None and u.get("pressure") is not None
    ]
    if updates:
        tank = pd.DataFrame(
            {
                "timestamp": [u["timestamp"] for u in updates],
                "pressure": [float(u["pressure"]) for u in updates],
            }
        ).sort_values("timestamp")
        frame = pd.merge_asof(
            frame.sort_values("timestamp"),
            tank,
            on="timestamp",
            direction="backward",
            tolerance=pd.Timedelta(seconds=60),
        )
    else:
        frame["pressure"] = None

    sac = dive_summary.get("avg_volume_sac")
    frame = frame.assign(
        dive_number=dive_number,
        trip_name="N/A",
        dive_site_name="N/A",
        rbt=None,
        sac_rate=float(sac) if sac else None,
        latitude=lat,
        longitude=lon,
    )
    numeric = ["time", "depth", "temperature", "pressure", "rbt", "ndl", "in_deco"]
    numeric += ["sac_rate", "latitude", "longitude"]
    frame[numeric] = frame[numeric].astype(float)  # None -> NaN, like Subsurface
    return frame[COLUMNS]


class GarminFitParser(DiveLogParser):
    def parse(self, file_path: str) -> pd.DataFrame:
        return messages_to_frame(read_messages(file_path), source=file_path)

    def supported_extensions(self) -> list[str]:
        return [".fit"]
