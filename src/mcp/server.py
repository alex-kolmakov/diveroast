"""DiveRoast MCP Server.

Exposes diving analysis tools via the Model Context Protocol so any
MCP-compatible client (Claude Desktop, Cursor, etc.) can use them.

Run standalone:
    python -m src.mcp.server            # stdio transport (default)
    python -m src.mcp.server --sse      # SSE transport on port 8001

The FastAPI gateway imports tool functions directly — no MCP overhead
for internal use.
"""

import json
import logging
from pathlib import Path

import pandas as pd
from mcp.server.fastmcp import FastMCP

from src.analysis.feature_engineering import extract_features
from src.parsers import get_parser
from src.rag.search import create_text_report, retrieve_context

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "DiveRoast",
    instructions=(
        "SCUBA dive analysis tools backed by DAN (Divers Alert Network) "
        "incident reports and safety guidelines. Upload a Subsurface dive "
        "log, then use the tools to analyze dives and roast bad habits."
    ),
)

# ---------------------------------------------------------------------------
# Session state — holds parsed dive data for the current MCP session
# ---------------------------------------------------------------------------
_dive_data: pd.DataFrame | None = None


def _get_dive_data() -> pd.DataFrame:
    if _dive_data is None:
        raise ValueError("No dive log loaded. Use parse_dive_log first.")
    return _dive_data


def _filter_dive(df: pd.DataFrame, dive_number: str) -> pd.DataFrame:
    """Filter DataFrame to a specific dive, handling type coercion."""
    import contextlib

    result = df[df["dive_number"] == dive_number]
    if result.empty:
        with contextlib.suppress(ValueError, TypeError):
            result = df[df["dive_number"] == int(dive_number)]
    return result


def _build_anomaly_keywords() -> str:
    """Build RAG-enriching keywords from objective dive anomalies in the current session.

    Mirrors DiverRoastAgent._build_anomaly_keywords() — adverse_conditions
    intentionally excluded (see note in conversation.py).
    """
    if _dive_data is None:
        return ""
    try:
        features = extract_features(_dive_data)
    except Exception:
        return ""
    keywords: list[str] = []
    if features["max_ascend_speed"].max() > 10:
        keywords.append("rapid ascent decompression sickness")
    if features["min_ndl"].min() < 1:
        keywords.append("close to deco stop NDL almost zero recreational limit")
    if features["sac_rate"].max() > 20:
        keywords.append("high air consumption SAC rate breathing")
    if features["max_depth"].max() > 30:
        keywords.append("deep diving incident")
    return " ".join(keywords)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search_dan_incidents(query: str) -> str:
    """Search DAN incident reports for diving incidents matching the query.

    Use this to find real-world incidents where divers experienced problems
    similar to what you see in a dive profile.
    """
    anomaly_kw = _build_anomaly_keywords()
    augmented = f"{query} {anomaly_kw}".strip() if anomaly_kw else query
    return retrieve_context(f"diving incident: {augmented}")


@mcp.tool()
def search_dan_guidelines(query: str) -> str:
    """Search DAN best practices and safety guidelines.

    Use this to find authoritative recommendations on diving safety topics
    like ascent rates, NDL management, air consumption, etc.
    """
    anomaly_kw = _build_anomaly_keywords()
    augmented = f"{query} {anomaly_kw}".strip() if anomaly_kw else query
    return retrieve_context(f"diving safety guideline: {augmented}")


@mcp.tool()
def parse_dive_log(file_path: str) -> str:
    """Parse a dive log file and store it for analysis.

    Supports Subsurface XML (.ssrf, .xml) formats. Returns a summary of
    dives found. After calling this, use analyze_dive_profile or
    get_dive_summary on individual dives.
    """
    global _dive_data
    # Resolve to absolute path and verify it stays within an allowed location.
    # This prevents path traversal attacks (e.g. /proc/self/environ on Linux).
    resolved = Path(file_path).resolve()
    allowed_roots = [Path.home(), Path("/tmp"), Path("/var/folders")]
    if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
        return f"Error: file_path must be within your home directory or /tmp. Got: {resolved}"
    if not resolved.exists():
        return f"Error: file not found: {resolved}"
    parser = get_parser(str(resolved))
    _dive_data = parser.parse(str(resolved))
    dive_numbers = sorted(_dive_data["dive_number"].unique().tolist())
    return (
        f"Parsed {len(dive_numbers)} dives: {dive_numbers}\n"
        f"Total samples: {len(_dive_data)}\n"
        f"Use analyze_dive_profile or get_dive_summary with a dive number."
    )


@mcp.tool()
def analyze_dive_profile(dive_number: str) -> str:
    """Analyze a specific dive's safety profile and flag issues.

    Checks for: high ascent rates (>10 m/min), dangerously low NDL (<5 min),
    high air consumption (SAC >20 l/min), deep dives (>30m), and adverse
    conditions. Returns detailed metrics and flagged issues.
    """
    df = _get_dive_data()
    dive_df = _filter_dive(df, dive_number)

    if dive_df.empty:
        return f"No data found for dive number {dive_number}."

    features = extract_features(dive_df)
    if features.empty:
        return f"Could not extract features for dive {dive_number}."

    row = features.iloc[0]
    issues: list[str] = []

    if row.get("max_ascend_speed", 0) > 10:
        issues.append(
            f"HIGH ASCENT RATE: {row['max_ascend_speed']:.1f} m/min "
            f"(recommended: <10 m/min). "
            f"{int(row.get('high_ascend_speed_count', 0))} fast instances."
        )
    if row.get("min_ndl", float("inf")) < 5:
        issues.append(
            f"DANGEROUSLY LOW NDL: {row['min_ndl']:.0f} minutes. "
            f"Cutting it close to mandatory deco."
        )
    if row.get("sac_rate", 0) > 20:
        issues.append(
            f"HIGH AIR CONSUMPTION: SAC {row['sac_rate']:.1f} l/min. "
            f"Work on breathing technique and buoyancy."
        )
    if row.get("max_depth", 0) > 30:
        issues.append(
            f"DEEP DIVE: {row['max_depth']:.1f}m max depth. "
            f"Ensure appropriate training and gas planning."
        )
    if row.get("adverse_conditions", 0) == 1:
        issues.append("Flagged as ADVERSE CONDITIONS (rating < 3).")

    summary = create_text_report(row.to_dict())
    if issues:
        return f"Dive {dive_number} Analysis:\n{summary}\n\nIssues:\n" + "\n".join(
            f"- {i}" for i in issues
        )
    return (
        f"Dive {dive_number} Analysis:\n{summary}\n\nNo major safety issues detected."
    )


@mcp.tool()
def get_dive_summary(dive_number: str) -> str:
    """Get a quick summary of a specific dive: location, depth, duration, SAC, rating."""
    df = _get_dive_data()
    dive_df = _filter_dive(df, dive_number)

    if dive_df.empty:
        return f"No data found for dive number {dive_number}."

    first = dive_df.iloc[0]
    return (
        f"Dive {dive_number}:\n"
        f"  Location: {first.get('dive_site_name', 'Unknown')} "
        f"({first.get('trip_name', 'Unknown')})\n"
        f"  Max Depth: {dive_df['depth'].max():.1f}m\n"
        f"  Duration: {dive_df['time'].max():.0f} seconds\n"
        f"  SAC Rate: {first.get('sac_rate', 'N/A')}\n"
        f"  Rating: {first.get('rating', 'N/A')}/5"
    )


@mcp.tool()
def list_dives() -> str:
    """List all dives in the currently loaded dive log with basic info."""
    df = _get_dive_data()
    dive_nums = sorted(df["dive_number"].unique().tolist())
    lines = []
    for dn in dive_nums:
        dive_df = _filter_dive(df, str(dn))
        first = dive_df.iloc[0]
        site = first.get("dive_site_name", "Unknown")
        max_depth = dive_df["depth"].max()
        rating = first.get("rating", "N/A")
        lines.append(f"  #{dn}: {site} — {max_depth:.1f}m max — rating {rating}/5")
    return f"Loaded dives ({len(dive_nums)}):\n" + "\n".join(lines)


@mcp.tool()
def refresh_dan_data() -> str:
    """Re-run the DAN content ingestion pipeline.

    Scrapes latest content from DAN WordPress API, vectorizes into LanceDB,
    and rebuilds the FTS index. This can take several minutes.
    """
    from src.rag.ingestion import run_pipeline

    table_name = run_pipeline()
    return f"DAN data refreshed. LanceDB table: {table_name}"


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------


@mcp.resource("diveroast://status")
def server_status() -> str:
    """Current server status: whether dive data is loaded, DAN index available."""
    import lancedb

    from src.config import settings

    status = {"dive_data_loaded": _dive_data is not None}
    if _dive_data is not None:
        status["dive_count"] = int(_dive_data["dive_number"].nunique())

    try:
        db = lancedb.connect(settings.LANCEDB_URI)
        tables = db.table_names()
        status["lancedb_tables"] = tables
        status["dan_index_ready"] = settings.LANCEDB_TABLE_NAME in tables
    except Exception:
        status["dan_index_ready"] = False

    return json.dumps(status, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
