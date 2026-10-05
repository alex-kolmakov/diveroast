import asyncio
import json
import logging
from typing import cast

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from google.genai import types

from src.agent.conversation import DiverRoastAgent
from src.agent.gemini_client import generate, get_client
from src.agent.usage import daily_budget, record_usage
from src.analysis.feature_engineering import (
    SHALLOW_ZONE_M,
    ascent_events,
    data_coverage,
    extract_features,
)
from src.api.dependencies import get_session, get_snapshot_store
from src.api.models import (
    AggregateStats,
    AscentEvent,
    DashboardResponse,
    DiveFeature,
    DiveMetricPoint,
    DiverProfile,
    MetricRange,
    ProblematicDive,
    ProfilePoint,
    SingleDive,
)
from src.config import settings
from src.storage.snapshots import SnapshotStore
from src.tools.dive import dive_issues, fmt, measured, thermal_flags

router = APIRouter()
logger = logging.getLogger(__name__)

# Safety thresholds: (label, unit, safe_upper, warning_upper)
THRESHOLDS = {
    "max_depth": ("Max Depth", "m", 18.0, 30.0),
    "max_ascend_speed": ("Max Sustained Ascent (30 s)", "m/min", 9.0, 10.0),
    "max_shallow_ascend_speed": ("Fastest Surfacing (last 5 m)", "m/min", 9.0, 10.0),
    "min_ndl": ("Min NDL", "min", None, None),  # inverted: lower is worse
    "sac_rate": ("SAC Rate", "L/min", 15.0, 20.0),
    "avg_temp": ("Avg Temperature", "\u00b0C", None, None),  # informational
}

# NDL thresholds are inverted: >10 safe, 5-10 warning, <5 danger
NDL_SAFE_LOWER = 10.0
NDL_WARNING_LOWER = 5.0

# Temperature: cold warning <10
TEMP_COLD_WARNING = 10.0

# What makes each issue category distinctive for "pick reason"
PICK_REASONS = {
    "rapid ascent": "Fastest ascent rate",
    "bolted to surface": "Fastest bolt to the surface",
    "low NDL": "Closest to decompression limit",
    "high air consumption": "Highest air consumption",
    "deep dive": "Deepest dive with issues",
}

# Which metric to rank by for each issue (and whether higher or lower is worse)
ISSUE_RANK_KEY: dict[str, tuple[str, bool]] = {
    "rapid ascent": ("max_ascend_speed", True),  # higher is worse
    "bolted to surface": ("max_shallow_ascend_speed", True),  # higher is worse
    "low NDL": ("ndl_rank", False),  # lower is worse; deco entry ranks lowest
    "high air consumption": ("sac_rate", True),  # higher is worse
    "deep dive": ("max_depth", True),  # higher is worse
}

# Region bounding boxes: (lat_min, lat_max, lon_min, lon_max)
REGION_BOXES = {
    "Red Sea": (12.0, 30.0, 32.0, 44.0),
    "Mediterranean": (30.0, 46.0, -6.0, 36.0),
    "Southeast Asia": (-11.0, 20.0, 95.0, 141.0),
    "Caribbean": (10.0, 27.0, -90.0, -59.0),
    "Central America": (7.0, 18.0, -92.0, -77.0),
    "South Pacific": (-25.0, 0.0, 150.0, 180.0),
    "North Atlantic": (40.0, 65.0, -80.0, 0.0),
    "Indian Ocean": (-35.0, 10.0, 40.0, 95.0),
    "East Africa": (-30.0, 5.0, 30.0, 55.0),
    "Australia": (-45.0, -10.0, 110.0, 155.0),
    "Japan": (24.0, 46.0, 122.0, 146.0),
    "Hawaii": (18.0, 23.0, -162.0, -154.0),
}

# Keywords in site/trip names that indicate water body type
WATER_TYPE_KEYWORDS = {
    "Quarry": ["quarry"],
    "Lake": ["lake", "lac", "see"],
    "Cave": ["cave", "cavern", "cenote"],
    "Wreck": ["wreck"],
    "River": ["river"],
}


def _classify_zone(value: float, safe_upper: float, warning_upper: float) -> str:
    if value <= safe_upper:
        return "safe"
    if value <= warning_upper:
        return "warning"
    return "danger"


def _classify_ndl_zone(value: float) -> str:
    if value >= NDL_SAFE_LOWER:
        return "safe"
    if value >= NDL_WARNING_LOWER:
        return "warning"
    return "danger"


def _classify_temp_zone(value: float) -> str:
    if value >= TEMP_COLD_WARNING:
        return "safe"
    return "warning"


def _classify_single_value(
    col: str, value: float, safe_up: float | None, warn_up: float | None
) -> str:
    """Classify a single value into a zone for a given metric column."""
    if col == "min_ndl":
        return _classify_ndl_zone(value)
    if col == "avg_temp":
        return _classify_temp_zone(value)
    assert safe_up is not None and warn_up is not None
    return _classify_zone(value, safe_up, warn_up)


def _r(value, ndigits: int = 2) -> float | None:
    """Round a recorded value; None if it wasn't recorded."""
    return round(float(value), ndigits) if measured(value) else None


def _build_metrics(features_df) -> list[MetricRange]:
    """Gauge data per metric, over only the dives that recorded it."""
    metrics = []
    total = len(features_df)
    for col, (label, unit, safe_up, warn_up) in THRESHOLDS.items():
        if col not in features_df.columns:
            continue
        series = features_df[col].dropna()
        recorded = len(series)
        min_val = float(series.min()) if recorded else None
        max_val = float(series.max()) if recorded else None
        avg_val = float(series.mean()) if recorded else None

        # Build per-dive values, sorted by value
        per_dive = []
        for _, row in features_df.iterrows():
            if not measured(row[col]):
                continue
            val = float(row[col])
            pt_zone = _classify_single_value(col, val, safe_up, warn_up)
            per_dive.append(
                DiveMetricPoint(
                    dive_number=str(row["dive_number"]),
                    value=round(val, 2),
                    zone=pt_zone,
                )
            )
        per_dive.sort(key=lambda p: p.value)

        common = {
            "label": label,
            "unit": unit,
            "recorded": recorded,
            "total": total,
            "min_val": min_val,
            "max_val": max_val,
            "avg_val": avg_val,
            "per_dive": per_dive,
        }
        if col == "min_ndl":
            metrics.append(
                MetricRange(
                    **common,
                    worst_val=min_val,  # lower is worse for NDL
                    safe_upper=NDL_SAFE_LOWER,
                    warning_upper=NDL_WARNING_LOWER,
                    zone=_classify_ndl_zone(min_val) if min_val is not None else "safe",
                )
            )
        elif col == "avg_temp":
            metrics.append(
                MetricRange(
                    **common,
                    worst_val=None,  # temperature is informational
                    safe_upper=TEMP_COLD_WARNING,
                    warning_upper=0.0,
                    zone=_classify_temp_zone(min_val)
                    if min_val is not None
                    else "safe",
                )
            )
        else:
            assert safe_up is not None and warn_up is not None
            metrics.append(
                MetricRange(
                    **common,
                    worst_val=max_val,  # higher is worse for depth, ascent, SAC
                    safe_upper=safe_up,
                    warning_upper=warn_up,
                    zone=_classify_zone(max_val, safe_up, warn_up)
                    if max_val is not None
                    else "safe",
                )
            )
    return metrics


def _compute_danger_score(row) -> float:
    """Step-weighted danger score from measured metrics only.

    Unrecorded metrics contribute nothing.
    """
    score = 0.0
    # NDL: lower is worse (weight 3). A computer-flagged deco entry is the
    # worst case.
    ndl = row.get("min_ndl")
    if row.get("entered_deco") or (measured(ndl) and ndl < NDL_WARNING_LOWER):
        score += 3.0 * 2
    elif measured(ndl) and ndl < NDL_SAFE_LOWER:
        score += 3.0

    # Sustained ascent speed (weight 2)
    ascent = row.get("max_ascend_speed", 0)
    if ascent > 10:
        score += 2.0 * 2
    elif ascent > 9:
        score += 2.0

    # Surfacing speed from the safety stop (weight 2): the largest relative
    # pressure change, so a bolt counts as much as a sustained fast ascent.
    surfacing = row.get("max_shallow_ascend_speed", 0)
    if surfacing > 15:
        score += 2.0 * 2
    elif surfacing > 10:
        score += 2.0

    # SAC rate (weight 1)
    sac = row.get("sac_rate")
    if measured(sac) and sac > 20:
        score += 1.0 * 2
    elif measured(sac) and sac > 15:
        score += 1.0

    # Depth (weight 1)
    depth = row.get("max_depth", 0)
    if depth > 30:
        score += 1.0 * 2
    elif depth > 18:
        score += 1.0

    return score


def _identify_issues(row) -> list[str]:
    issues = []
    if row.get("max_ascend_speed", 0) > 9:
        issues.append("rapid ascent")
    if row.get("max_shallow_ascend_speed", 0) > 10:
        issues.append("bolted to surface")
    ndl = row.get("min_ndl")
    if row.get("entered_deco") or (measured(ndl) and ndl < NDL_SAFE_LOWER):
        issues.append("low NDL")
    sac = row.get("sac_rate")
    if measured(sac) and sac > 15:
        issues.append("high air consumption")
    if row.get("max_depth", 0) > 30:
        issues.append("deep dive")
    flags = thermal_flags(row)
    if flags["prolonged_cold"]:
        issues.append("prolonged cold")
    if flags["cold_stops"]:
        issues.append("cold stops")
    if flags["long_warm"]:
        issues.append("long warm dive")
    return issues


def _generate_dive_summaries(
    dives: list[dict],
) -> list[str]:
    """Ask Gemini to write a short paragraph for each problematic dive.

    Each dict in *dives* has keys: dive_number, site, pick_reason, issues, stats.
    Returns one summary string per dive (same order).
    Falls back to a simple template if the LLM call fails.
    """
    prompt_parts = [
        "You are a salty old divemaster roasting a dive log. For each dive below, "
        "write ONE jab of 25 words or fewer. Harsh, dry and funny, in diver lingo "
        "(bolting, corking, riding the NDL, bent, air hog, bounce dive). "
        "Build it on the one number that got the dive picked and say what that "
        "number risks, keeping the physiology right and the heat proportionate: "
        "barely over a limit gets a light jab. The card already shows the dive number, the site and why "
        "it was picked, so don't repeat them. Roast the diving, never the person. "
        "Give every dive a different image and sentence shape, drawn from that "
        "dive's own site, depth or conditions; no missiles, rockets or chamber "
        "rides. "
        "Only quote numbers given below; a value marked 'not recorded' was not "
        "logged, so say nothing about it.\n"
        "Return a JSON array of strings, one per dive, in the same order.\n"
    ]
    for i, d in enumerate(dives):
        prompt_parts.append(
            f"\nDive {i + 1}: #{d['dive_number']} at {d['site']}\n"
            f"  Picked for: {d['pick_reason']}\n"
            f"  Issues: {', '.join(d['issues'])}\n"
            f"  Stats: max_depth={d['stats']['max_depth']:.1f}m, "
            f"max_ascent={fmt(d['stats'].get('max_ascend_speed'), unit=' m/min')} "
            f"(30 s average), "
            f"fastest_surfacing="
            f"{fmt(d['stats'].get('max_shallow_ascend_speed'), unit=' m/min')} "
            f"(from the safety stop, the last {SHALLOW_ZONE_M:.0f} m), "
            f"entered_deco={'yes' if d['stats'].get('entered_deco') else 'no'}, "
            f"min_ndl={fmt(d['stats'].get('min_ndl'), '.0f', ' min')}, "
            f"sac_rate={fmt(d['stats'].get('sac_rate'), unit=' L/min')}, "
            f"avg_temp={fmt(d['stats'].get('avg_temp'), unit='°C')}, "
            f"temp_gradient={fmt(d['stats'].get('temp_gradient'), unit='°C')}"
        )

    try:
        if daily_budget.spent():
            raise RuntimeError("daily token budget spent")
        # One retry only: the dashboard waits on this call, and the template
        # below is a fine answer when the model is busy.
        response = generate(
            get_client(),
            attempts=2,
            contents="\n".join(prompt_parts),
            config=types.GenerateContentConfig(
                max_output_tokens=settings.SUMMARY_MAX_OUTPUT_TOKENS
            ),
        )
        record_usage(response, "dive summaries")
        text = (response.text or "").strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        summaries = json.loads(text.strip())
        if isinstance(summaries, list) and len(summaries) == len(dives):
            return [str(s) for s in summaries]
    except Exception:
        logger.warning(
            "LLM dive summary generation failed, using fallback", exc_info=True
        )

    # Fallback: simple template
    fallback = []
    for d in dives:
        fallback.append(
            f"Dive #{d['dive_number']} at {d['site']} was flagged for "
            f"{', '.join(d['issues'][:3])}."
        )
    return fallback


def _classify_water_type(avg_temp: float) -> str:
    """Classify water type from average temperature."""
    if avg_temp > 24:
        return "Tropical"
    if avg_temp >= 15:
        return "Temperate"
    return "Cold water"


def _classify_region(lat: float, lon: float) -> str | None:
    """Classify a dive region from lat/lon using bounding boxes."""
    for region, (lat_min, lat_max, lon_min, lon_max) in REGION_BOXES.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return region
    return None


def _classify_experience(dive_count: int, max_depth: float) -> str:
    """Classify experience level from dive count and max depth."""
    if dive_count >= 100 or max_depth > 40:
        return "advanced"
    if dive_count >= 30 or max_depth > 25:
        return "intermediate"
    return "beginner"


MAX_PROFILE_POINTS = 600


def _build_single_dive(dive_data, row) -> SingleDive:
    """Profile, fast-ascent events and issues for a one-dive log."""
    dive = dive_data.sort_values("time")
    times = dive["time"].to_numpy(dtype=float)
    depths = dive["depth"].to_numpy(dtype=float)
    step = max(1, len(dive) // MAX_PROFILE_POINTS)
    keep = list(range(0, len(dive), step))
    if keep[-1] != len(dive) - 1:
        keep.append(len(dive) - 1)  # always end at the surfacing
    sampled = dive.iloc[keep]
    return SingleDive(
        dive_number=str(row["dive_number"]),
        duration_min=round(float(times[-1] - times[0]) / 60, 1),
        profile=[
            ProfilePoint(
                time_s=float(p["time"]),
                depth=round(float(p["depth"]), 2),
                temperature=_r(p.get("temperature"), 1),
            )
            for _, p in sampled.iterrows()
        ],
        ascent_events=[
            AscentEvent(
                kind=e["kind"],
                start_s=round(e["start"], 1),
                end_s=round(e["end"], 1),
                rate=round(e["rate"], 1),
            )
            for e in ascent_events(times, depths)
        ],
        issues=dive_issues(row),
    )


def _build_diver_profile(features_df) -> DiverProfile:
    """Build a diver profile from aggregated dive features."""
    water_types = set()
    regions = set()
    dive_sites = []
    temp_exposure: dict[str, int] = {
        "Tropical (>24°C)": 0,
        "Temperate (15-24°C)": 0,
        "Cold (<15°C)": 0,
    }

    for _, row in features_df.iterrows():
        # Water type from temperature
        avg_temp = row.get("avg_temp")
        if measured(avg_temp) and avg_temp > 0:
            water_type = _classify_water_type(avg_temp)
            water_types.add(water_type)
            if avg_temp > 24:
                temp_exposure["Tropical (>24°C)"] += 1
            elif avg_temp >= 15:
                temp_exposure["Temperate (15-24°C)"] += 1
            else:
                temp_exposure["Cold (<15°C)"] += 1

        # Water type from site/trip name keywords (environment type, not temperature band)
        site_name = str(row.get("dive_site_name", ""))
        trip_name = str(row.get("trip_name", ""))
        combined = f"{site_name} {trip_name}".lower()
        for wtype, keywords in WATER_TYPE_KEYWORDS.items():
            if any(kw in combined for kw in keywords):
                water_types.add(wtype)

        # Collect unique site names
        if site_name and site_name != "N/A" and site_name not in dive_sites:
            dive_sites.append(site_name)

        # Region from coordinates
        lat = row.get("latitude")
        lon = row.get("longitude")
        if measured(lat) and measured(lon) and lat != 0 and lon != 0:
            region = _classify_region(float(lat), float(lon))
            if region:
                regions.add(region)

    max_depth = float(features_df["max_depth"].max()) if len(features_df) > 0 else 0
    experience_level = _classify_experience(len(features_df), max_depth)

    return DiverProfile(
        water_types=sorted(water_types),
        regions=sorted(regions),
        experience_level=experience_level,
        dive_sites=dive_sites,
        temp_exposure={k: v for k, v in temp_exposure.items() if v > 0},
    )


@router.get("/api/dashboard/{session_id}", response_model=DashboardResponse)
async def get_dashboard(
    session_id: str,
    background_tasks: BackgroundTasks,
    store: SnapshotStore = Depends(get_snapshot_store),
):
    """Compute dashboard data from session dive data.

    Computed (and the Gemini summaries generated) once per uploaded log, then
    served from the session cache. Saves a read-only snapshot under the
    session's separate ``share_id`` for /api/shared/{share_id}.
    """
    agent = get_session(session_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if agent.dive_data is None:
        raise HTTPException(status_code=400, detail="No dive data in session")
    # The lock stops a double fetch (React StrictMode, a reload) from paying
    # for the Gemini summaries twice; the build runs in a thread so it
    # doesn't stall every other request.
    async with agent.dashboard_lock:
        if agent.dashboard is None:
            response = await asyncio.to_thread(
                _build_dashboard, agent, agent.dive_data, session_id
            )
            agent.dashboard = response
            # Persist the public snapshot under the share ID, without the session ID
            background_tasks.add_task(
                store.save,
                agent.share_id,
                response.model_copy(update={"session_id": None}),
            )
    return agent.dashboard.model_copy(update={"session_id": session_id})


def _build_dashboard(
    agent: DiverRoastAgent, dive_data: pd.DataFrame, session_id: str
) -> DashboardResponse:
    """Compute the dashboard for the session's log (blocking: runs in a thread)."""
    features_df = extract_features(dive_data)
    # Rank key for the "low NDL" pick: a deco entry is worse than any NDL.
    features_df["ndl_rank"] = features_df["min_ndl"].where(
        ~features_df["entered_deco"], -1.0
    )

    # Build per-dive features list
    all_dives = []
    for _, row in features_df.iterrows():
        lat = row.get("latitude")
        lon = row.get("longitude")
        all_dives.append(
            DiveFeature(
                dive_number=str(row["dive_number"]),
                avg_depth=round(float(row["avg_depth"]), 2),
                max_depth=round(float(row["max_depth"]), 2),
                depth_variability=_r(row["depth_variability"]),
                avg_temp=_r(row["avg_temp"]),
                max_temp=_r(row["max_temp"]),
                min_temp=_r(row["min_temp"]),
                temp_gradient=_r(row["temp_gradient"]),
                temp_variability=_r(row["temp_variability"]),
                dive_minutes=_r(row["dive_minutes"], 1),
                water_min_temp=_r(row["water_min_temp"], 1),
                cold_minutes=_r(row["cold_minutes"], 1),
                stop_temp=_r(row["stop_temp"], 1),
                avg_pressure=_r(row["avg_pressure"]),
                max_pressure=_r(row["max_pressure"]),
                pressure_variability=_r(row["pressure_variability"]),
                min_ndl=_r(row["min_ndl"]),
                entered_deco=bool(row["entered_deco"]),
                sac_rate=_r(row["sac_rate"]),
                max_ascend_speed=_r(row["max_ascend_speed"]),
                high_ascend_speed_count=round(float(row["high_ascend_speed_count"]), 0),
                max_shallow_ascend_speed=_r(row["max_shallow_ascend_speed"]),
                shallow_bolt_count=int(row["shallow_bolt_count"]),
                dive_site_name=str(row.get("dive_site_name", "N/A")),
                trip_name=str(row.get("trip_name", "N/A")),
                latitude=_r(lat, 6) if measured(lat) and lat != 0 else None,
                longitude=_r(lon, 6) if measured(lon) and lon != 0 else None,
            )
        )

    # Compute metrics with per-dive values
    metrics = _build_metrics(features_df)

    # Compute aggregate stats
    aggregate_stats = AggregateStats(
        total_dives=len(features_df),
        avg_max_depth=round(float(features_df["max_depth"].mean()), 2),
        avg_sac_rate=_r(features_df["sac_rate"].mean()),
        avg_max_ascend_speed=_r(features_df["max_ascend_speed"].mean()),
        dives_with_fast_ascent=int((features_df["max_ascend_speed"] > 10).sum()),
        dives_with_shallow_bolt=int(
            (features_df["max_shallow_ascend_speed"] > 10).sum()
        ),
        data_coverage=data_coverage(features_df),
    )

    # One dive (e.g. a Garmin FIT file): show that dive in detail instead of
    # ranking "worst dives", and skip the Gemini call for their summaries.
    single = len(features_df) == 1

    # Compute danger scores for all dives
    scored_dives = []
    for _, row in features_df.iterrows():
        row_dict = row.to_dict()
        score = _compute_danger_score(row_dict)
        if score > 0:
            issues = _identify_issues(row_dict)
            scored_dives.append((str(row["dive_number"]), score, row_dict, issues))

    scored_dives.sort(key=lambda x: x[1], reverse=True)

    # Pick top 3 for different primary reasons where possible
    picks: list[
        tuple[str, float, dict, list[str], str]
    ] = []  # (dn, score, row, issues, pick_issue)
    used_dive_nums: set[str] = set()
    used_pick_issues: set[str] = set()

    # First pass: for each issue category, pick the dive with the worst
    # value for that specific metric (not overall danger score)
    for pick_issue, (rank_col, higher_is_worse) in ISSUE_RANK_KEY.items():
        if len(picks) >= 3:
            break
        best: tuple[str, float, dict, list[str], str] | None = None
        best_metric_val: float = 0.0
        for dive_num, score, row_dict, issues in scored_dives:
            if dive_num in used_dive_nums:
                continue
            val = row_dict.get(rank_col)
            if not measured(val):
                continue
            if pick_issue in issues and pick_issue not in used_pick_issues:
                val = float(cast(float, val))
                if best is None or (
                    (higher_is_worse and val > best_metric_val)
                    or (not higher_is_worse and val < best_metric_val)
                ):
                    best = (dive_num, score, row_dict, issues, pick_issue)
                    best_metric_val = val
        if best:
            used_dive_nums.add(best[0])
            used_pick_issues.add(best[4])
            picks.append(best)

    # Second pass: fill remaining slots from highest overall danger score
    for dive_num, score, row_dict, issues in scored_dives:
        if len(picks) >= 3:
            break
        if dive_num in used_dive_nums:
            continue
        primary = issues[0] if issues else "rapid ascent"
        picks.append((dive_num, score, row_dict, issues, primary))
        used_dive_nums.add(dive_num)

    # Sort picks by danger score
    picks.sort(key=lambda x: x[1], reverse=True)
    if single:
        picks = []

    # Generate LLM summaries for all picks in one call
    llm_inputs = []
    for dn, _sc, rd, iss, pi in picks:
        site = rd.get("dive_site_name", "N/A")
        llm_inputs.append(
            {
                "dive_number": dn,
                "site": site if site and site != "N/A" else "unknown site",
                "pick_reason": PICK_REASONS.get(pi, "Most dangerous overall"),
                "issues": iss,
                "stats": rd,
            }
        )

    summaries = _generate_dive_summaries(llm_inputs) if llm_inputs else []

    top_problematic_dives = []
    for i, (dn, sc, _rd, iss, pi) in enumerate(picks):
        feature = next(d for d in all_dives if d.dive_number == dn)
        top_problematic_dives.append(
            ProblematicDive(
                dive_number=dn,
                danger_score=round(sc, 2),
                features=feature,
                issues=iss,
                summary=summaries[i] if i < len(summaries) else "",
                pick_reason=PICK_REASONS.get(pi, "Most dangerous overall"),
            )
        )

    # Build diver profile
    diver_profile = _build_diver_profile(features_df)

    response = DashboardResponse(
        session_id=session_id,
        share_id=agent.share_id,
        aggregate_stats=aggregate_stats,
        metrics=metrics,
        all_dives=all_dives,
        top_problematic_dives=top_problematic_dives,
        diver_profile=diver_profile,
        roast_summary=agent.roast_summary,
        roast_prompt=agent.roast_prompt,
        roast_sources=agent.roast_sources,
        mode="single" if single else "log",
        single_dive=_build_single_dive(dive_data, features_df.iloc[0])
        if single
        else None,
    )
    return response
