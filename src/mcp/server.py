"""DiveRoast MCP Server.

Exposes diving analysis tools (implemented once in ``src.tools``) via the Model Context Protocol so any
MCP-compatible client (Claude Desktop, Cursor, etc.) can use them.

Run standalone:
    python -m src.mcp.server            # stdio transport (default)
    python -m src.mcp.server --sse      # SSE transport on port 8001

The FastAPI gateway imports tool functions directly — no MCP overhead
for internal use.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

import pandas as pd
from mcp.server.fastmcp import FastMCP

from src.analysis.feature_engineering import extract_features
from src.parsers import get_parser
from src.tools import dan, dive

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


def _allowed_roots() -> list[Path]:
    """Directories parse_dive_log may read from.

    Override with DIVEROAST_MCP_ALLOWED_ROOTS (os.pathsep-separated).
    """
    configured = os.environ.get("DIVEROAST_MCP_ALLOWED_ROOTS")
    if configured:
        roots = [Path(p).expanduser() for p in configured.split(os.pathsep) if p]
    else:
        home = Path.home()
        roots = [
            home / "Downloads",
            home / "Documents",
            home / "Desktop",
            Path(tempfile.gettempdir()),
            Path("/tmp"),
        ]
    return [r.resolve() for r in roots]


def _get_dive_data() -> pd.DataFrame:
    if _dive_data is None:
        raise ValueError("No dive log loaded. Use parse_dive_log first.")
    return _dive_data


def _get_features() -> pd.DataFrame:
    return extract_features(_get_dive_data())


def _filter_dive(df: pd.DataFrame, dive_number: str) -> pd.DataFrame:
    """Filter DataFrame to a specific dive, handling type coercion."""
    return dive.filter_dive(df, dive_number)


def _build_anomaly_keywords() -> str:
    """RAG-enriching keywords from measured anomalies in the current session."""
    if _dive_data is None:
        return ""
    try:
        features = extract_features(_dive_data)
    except Exception:
        logger.exception("Feature extraction failed while building RAG keywords")
        return ""
    return dive.build_anomaly_keywords(features)


def _augment(query: str) -> str:
    anomaly_kw = _build_anomaly_keywords()
    return f"{query} {anomaly_kw}".strip() if anomaly_kw else query


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search_dan_incidents(query: str) -> str:
    """Search DAN incident reports for diving incidents matching the query.

    Use this to find real-world incidents where divers experienced problems
    similar to what you see in a dive profile. Each result carries its DAN
    source link.
    """
    return dan.search_dan_incidents(_augment(query)).text


@mcp.tool()
def search_dan_guidelines(query: str) -> str:
    """Search DAN best practices and safety guidelines.

    Use this to find authoritative recommendations on diving safety topics
    like ascent rates, NDL management, air consumption, etc. Each result
    carries its DAN source link.
    """
    return dan.search_dan_guidelines(_augment(query)).text


@mcp.tool()
def parse_dive_log(file_path: str) -> str:
    """Parse a dive log file and store it for analysis.

    Supports Subsurface XML (.ssrf, .xml) formats. Returns a summary of
    dives found. After calling this, use analyze_dive_profile or
    get_dive_summary on individual dives.
    """
    global _dive_data
    # Resolve symlinks and '..' first, then require the path to sit inside an
    # allowed root. is_relative_to compares path components, so a sibling like
    # /tmpfoo doesn't pass as /tmp.
    resolved = Path(file_path).expanduser().resolve()
    roots = _allowed_roots()
    if not any(resolved.is_relative_to(root) for root in roots):
        allowed = ", ".join(str(r) for r in roots)
        return f"Error: file_path must be within one of: {allowed}. Got: {resolved}"
    if not resolved.is_file():
        return f"Error: file not found: {resolved}"
    parser = get_parser(str(resolved))
    _dive_data = parser.parse(str(resolved))
    dive_numbers = sorted(
        (str(d) for d in _dive_data["dive_number"].unique()), key=dive.dive_sort_key
    )
    return (
        f"Parsed {len(dive_numbers)} dives: {dive_numbers}\n"
        f"Total samples: {len(_dive_data)}\n"
        f"{dive.coverage_line(extract_features(_dive_data))}\n"
        f"Use analyze_dive_profile or get_dive_summary with a dive number."
    )


@mcp.tool()
def analyze_dive_profile(dive_number: str) -> str:
    """Analyze a specific dive's safety profile and flag issues.

    Checks for: high sustained ascent rates (>10 m/min over 30 s), fast
    surfacings (>10 m/min through the last 8 m), deco entry,
    dangerously low NDL (<5 min), high air consumption (SAC >20 L/min),
    and deep dives (>30m). Metrics the dive computer
    didn't record are reported as not recorded.
    """
    return dive.analyze_dive_profile(_get_dive_data(), dive_number, _get_features())


@mcp.tool()
def get_dive_summary(dive_number: str) -> str:
    """Get a quick summary of a specific dive: location, depth, duration, SAC."""
    return dive.get_dive_summary(_get_dive_data(), dive_number, _get_features())


@mcp.tool()
def list_dives() -> str:
    """List all dives in the currently loaded dive log with basic info."""
    return dive.list_dives(_get_dive_data(), _get_features())


@mcp.tool()
def analyze_all_dives() -> str:
    """Analyze all loaded dives: aggregate stats, safety concerns, worst offenders."""
    return dive.analyze_all_dives(_get_dive_data(), _get_features())


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
