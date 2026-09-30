"""Gemini function-calling surface for the DiveRoast agent.

The implementations live in ``src.tools`` (shared with the MCP server). Dive
tools take the session's parsed DataFrame and its precomputed features,
injected by ``DiverRoastAgent._execute_tool``.
"""

from google.genai import types

from src.tools import dan, dive

# Tools that need the session's dive data injected as ``dive_data``/``features``
DIVE_DATA_TOOLS = frozenset(
    {"analyze_dive_profile", "get_dive_summary", "list_dives", "analyze_all_dives"}
)

# --- Gemini function declarations ---

TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="search_dan_incidents",
        description="Search the DAN (Divers Alert Network) database for diving incident reports. Use this to find real incidents related to the diver's behavior.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "query": types.Schema(
                    type=types.Type.STRING,
                    description="Search query describing the incident type (e.g., 'rapid ascent decompression sickness')",
                ),
            },
            required=["query"],
        ),
    ),
    types.FunctionDeclaration(
        name="search_dan_guidelines",
        description="Search the DAN database for diving safety guidelines and best practices.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "query": types.Schema(
                    type=types.Type.STRING,
                    description="Search query for safety guidelines (e.g., 'ascent rate recommendations')",
                ),
            },
            required=["query"],
        ),
    ),
    types.FunctionDeclaration(
        name="analyze_dive_profile",
        description="Analyze a specific dive's profile data and flag any safety issues like high ascent rate, low NDL, deco entry, or high air consumption. Metrics the dive computer did not record are reported as not recorded. The dive data is automatically available from the uploaded dive log.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "dive_number": types.Schema(
                    type=types.Type.STRING,
                    description="The dive number to analyze",
                ),
            },
            required=["dive_number"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_dive_summary",
        description="Get a quick summary of a specific dive including location, max depth, duration, and SAC rate. The dive data is automatically available from the uploaded dive log.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "dive_number": types.Schema(
                    type=types.Type.STRING,
                    description="The dive number to summarize",
                ),
            },
            required=["dive_number"],
        ),
    ),
    types.FunctionDeclaration(
        name="list_dives",
        description="List all dives in the uploaded dive log with site name and max depth. Use this to give the diver an overview of their log before diving into specifics.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={},
        ),
    ),
    types.FunctionDeclaration(
        name="analyze_all_dives",
        description="Analyze ALL dives in the uploaded log at once: aggregate stats, safety concern percentages, and top worst offenders. Use this when the diver asks for an overall roast, pattern analysis, or holistic review of their dive log.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={},
        ),
    ),
]

TOOL_FUNCTIONS = {
    "search_dan_incidents": dan.search_dan_incidents,
    "search_dan_guidelines": dan.search_dan_guidelines,
    "analyze_dive_profile": lambda dive_number, dive_data, features=None: (
        dive.analyze_dive_profile(dive_data, dive_number, features)
    ),
    "get_dive_summary": lambda dive_number, dive_data, features=None: (
        dive.get_dive_summary(dive_data, dive_number, features)
    ),
    "list_dives": lambda dive_data, features=None: dive.list_dives(dive_data, features),
    "analyze_all_dives": lambda dive_data, features=None: dive.analyze_all_dives(
        dive_data, features
    ),
}
