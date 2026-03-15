import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from src.agent.gemini_client import get_client
from src.analysis.feature_engineering import extract_features
from src.api.dependencies import get_session, get_snapshot_store
from src.api.models import (
    AggregateStats,
    DashboardResponse,
    DiveFeature,
    DiveMetricPoint,
    DiverProfile,
    MetricRange,
    ProblematicDive,
)
from src.config import settings
from src.storage.snapshots import SnapshotStore

router = APIRouter()
logger = logging.getLogger(__name__)

# Safety thresholds: (label, unit, safe_upper, warning_upper)
THRESHOLDS = {
    "max_depth": ("Max Depth", "m", 18.0, 30.0),
    "max_ascend_speed": ("Max Ascent Speed", "m/min", 9.0, 10.0),
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
    "low NDL": "Closest to decompression limit",
    "high air consumption": "Highest air consumption",
    "deep dive": "Deepest dive with issues",
    "adverse conditions": "Worst conditions",
}

# Which metric to rank by for each issue (and whether higher or lower is worse)
ISSUE_RANK_KEY: dict[str, tuple[str, bool]] = {
    "rapid ascent": ("max_ascend_speed", True),  # higher is worse
    "low NDL": ("min_ndl", False),  # lower is worse
    "high air consumption": ("sac_rate", True),  # higher is worse
    "deep dive": ("max_depth", True),  # higher is worse
    "adverse conditions": ("adverse_conditions", True),  # higher is worse
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


def _build_metrics(features_df) -> list[MetricRange]:
    metrics = []
    for col, (label, unit, safe_up, warn_up) in THRESHOLDS.items():
        if col not in features_df.columns:
            continue
        series = features_df[col]
        min_val = float(series.min())
        max_val = float(series.max())
        avg_val = float(series.mean())

        # Build per-dive values, sorted by value
        per_dive = []
        for _, row in features_df.iterrows():
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

        if col == "min_ndl":
            zone = _classify_ndl_zone(min_val)
            worst_val = min_val  # lower is worse for NDL
            metrics.append(
                MetricRange(
                    label=label,
                    unit=unit,
                    min_val=min_val,
                    max_val=max_val,
                    avg_val=avg_val,
                    worst_val=worst_val,
                    safe_upper=NDL_SAFE_LOWER,
                    warning_upper=NDL_WARNING_LOWER,
                    zone=zone,
                    per_dive=per_dive,
                )
            )
        elif col == "avg_temp":
            zone = _classify_temp_zone(min_val)
            metrics.append(
                MetricRange(
                    label=label,
                    unit=unit,
                    min_val=min_val,
                    max_val=max_val,
                    avg_val=avg_val,
                    worst_val=None,  # temperature is informational
                    safe_upper=TEMP_COLD_WARNING,
                    warning_upper=0.0,
                    zone=zone,
                    per_dive=per_dive,
                )
            )
        else:
            assert safe_up is not None and warn_up is not None
            zone = _classify_zone(max_val, safe_up, warn_up)
            worst_val = max_val  # higher is worse for depth, ascent speed, SAC
            metrics.append(
                MetricRange(
                    label=label,
                    unit=unit,
                    min_val=min_val,
                    max_val=max_val,
                    avg_val=avg_val,
                    worst_val=worst_val,
                    safe_upper=safe_up,
                    warning_upper=warn_up,
                    zone=zone,
                    per_dive=per_dive,
                )
            )
    return metrics


def _compute_danger_score(row) -> float:
    score = 0.0
    # NDL: lower is worse (weight 3)
    if row.get("min_ndl", 999) < NDL_WARNING_LOWER:
        score += 3.0 * 2
    elif row.get("min_ndl", 999) < NDL_SAFE_LOWER:
        score += 3.0

    # Ascent speed (weight 2)
    ascent = row.get("max_ascend_speed", 0)
    if ascent > 10:
        score += 2.0 * 2
    elif ascent > 9:
        score += 2.0

    # SAC rate (weight 1)
    sac = row.get("sac_rate", 0)
    if sac > 20:
        score += 1.0 * 2
    elif sac > 15:
        score += 1.0

    # Depth (weight 1)
    depth = row.get("max_depth", 0)
    if depth > 30:
        score += 1.0 * 2
    elif depth > 18:
        score += 1.0

    # Adverse conditions (weight 5)
    if row.get("adverse_conditions", 0):
        score += 5.0

    return score


def _identify_issues(row) -> list[str]:
    issues = []
    if row.get("max_ascend_speed", 0) > 9:
        issues.append("rapid ascent")
    if row.get("min_ndl", 999) < NDL_SAFE_LOWER:
        issues.append("low NDL")
    if row.get("sac_rate", 0) > 15:
        issues.append("high air consumption")
    if row.get("max_depth", 0) > 30:
        issues.append("deep dive")
    if row.get("adverse_conditions", 0):
        issues.append("adverse conditions")
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
        "You are a diving safety analyst. For each dive below, write ONE concise paragraph "
        "(30-50 words) explaining why it was picked as one of the worst dives. "
        "Reference the dive site by name, mention the specific numbers that are concerning, "
        "and explain the real-world risk. Be direct and factual, not dramatic.\n"
        "Return a JSON array of strings, one per dive, in the same order.\n"
    ]
    for i, d in enumerate(dives):
        prompt_parts.append(
            f"\nDive {i + 1}: #{d['dive_number']} at {d['site']}\n"
            f"  Picked for: {d['pick_reason']}\n"
            f"  Issues: {', '.join(d['issues'])}\n"
            f"  Stats: max_depth={d['stats']['max_depth']:.1f}m, "
            f"max_ascent={d['stats']['max_ascend_speed']:.1f} m/min, "
            f"min_ndl={d['stats']['min_ndl']:.0f} min, "
            f"sac_rate={d['stats']['sac_rate']:.1f} L/min, "
            f"avg_temp={d['stats']['avg_temp']:.1f}°C, "
            f"temp_gradient={d['stats']['temp_gradient']:.1f}°C"
        )

    try:
        client = get_client()
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents="\n".join(prompt_parts),
        )
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
        avg_temp = float(row.get("avg_temp", 0))
        if avg_temp > 0:
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
        if lat is not None and lon is not None and lat != 0 and lon != 0:
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
    """Compute dashboard data from session dive data. Saves a snapshot for sharing."""
    agent = get_session(session_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if agent.dive_data is None:
        raise HTTPException(status_code=400, detail="No dive data in session")

    features_df = extract_features(agent.dive_data)

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
                depth_variability=round(float(row["depth_variability"]), 2),
                avg_temp=round(float(row["avg_temp"]), 2),
                max_temp=round(float(row["max_temp"]), 2),
                min_temp=round(float(row["min_temp"]), 2),
                temp_gradient=round(float(row["temp_gradient"]), 2),
                temp_variability=round(float(row["temp_variability"]), 2),
                avg_pressure=round(float(row["avg_pressure"]), 2),
                max_pressure=round(float(row["max_pressure"]), 2),
                pressure_variability=round(float(row["pressure_variability"]), 2),
                min_ndl=round(float(row["min_ndl"]), 2),
                sac_rate=round(float(row["sac_rate"]), 2),
                rating=round(float(row["rating"]), 1),
                max_ascend_speed=round(float(row["max_ascend_speed"]), 2),
                high_ascend_speed_count=round(float(row["high_ascend_speed_count"]), 0),
                adverse_conditions=int(row["adverse_conditions"]),
                dive_site_name=str(row.get("dive_site_name", "N/A")),
                trip_name=str(row.get("trip_name", "N/A")),
                latitude=round(float(lat), 6) if lat is not None and lat != 0 else None,
                longitude=round(float(lon), 6)
                if lon is not None and lon != 0
                else None,
            )
        )

    # Compute metrics with per-dive values
    metrics = _build_metrics(features_df)

    # Compute aggregate stats
    aggregate_stats = AggregateStats(
        total_dives=len(features_df),
        avg_max_depth=round(float(features_df["max_depth"].mean()), 2),
        avg_sac_rate=round(float(features_df["sac_rate"].mean()), 2),
        avg_max_ascend_speed=round(float(features_df["max_ascend_speed"].mean()), 2),
        dives_with_adverse_conditions=int(features_df["adverse_conditions"].sum()),
    )

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
    for pick_issue in [
        "rapid ascent",
        "low NDL",
        "high air consumption",
        "deep dive",
        "adverse conditions",
    ]:
        if len(picks) >= 3:
            break
        rank_col, higher_is_worse = ISSUE_RANK_KEY[pick_issue]
        best: tuple[str, float, dict, list[str], str] | None = None
        best_metric_val: float = 0.0
        for dive_num, score, row_dict, issues in scored_dives:
            if dive_num in used_dive_nums:
                continue
            if pick_issue in issues and pick_issue not in used_pick_issues:
                val = float(row_dict.get(rank_col, 0))
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

    # Generate LLM summaries for all picks in one call
    llm_inputs = []
    for dn, _sc, rd, iss, pi in picks:
        site = rd.get("dive_site_name", "N/A")
        llm_inputs.append(
            {
                "dive_number": dn,
                "site": site if site and site != "N/A" else "unknown site",
                "pick_reason": PICK_REASONS.get(pi, "Most dangerous overall"),
                "issues": [i for i in iss if i != "adverse conditions"],
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
        aggregate_stats=aggregate_stats,
        metrics=metrics,
        all_dives=all_dives,
        top_problematic_dives=top_problematic_dives,
        diver_profile=diver_profile,
    )

    # Persist snapshot so this session can be shared via /api/shared/{session_id}
    background_tasks.add_task(store.save, session_id, response)

    return response
