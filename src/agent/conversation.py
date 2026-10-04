import asyncio
import logging
import re
import secrets
from collections.abc import AsyncGenerator
from typing import Any

import pandas as pd
from google.genai import types
from openinference.instrumentation import using_attributes

from src.agent.gemini_client import get_client
from src.agent.system_prompts import PromptVersion, get_active_prompt
from src.agent.tools import DIVE_DATA_TOOLS, TOOL_DECLARATIONS, TOOL_FUNCTIONS
from src.analysis.feature_engineering import extract_features
from src.config import settings
from src.observability import get_tracer
from src.rag.search import Retrieval
from src.tools import dan
from src.tools.dive import (
    anomaly_queries,
    build_anomaly_keywords,
    coverage_line,
    fmt,
    measured,
)

logger = logging.getLogger(__name__)

FORCE_ANSWER_NOTE = (
    "[System: tool-call limit reached. Answer now using the tool results "
    "you already have.]"
)


# Chunks kept per query in the search that runs before every answer.
PRIOR_SEARCH_TOP_K = 3

DAN_MATERIAL_NOTE = (
    "\n\n# DAN material\n"
    "Already searched for this message. Cite it only where it backs your "
    "point, using the source title and URL shown; a hit that doesn't fit is "
    "not a citation.\n\n{material}"
)
NO_DAN_MATERIAL_NOTE = (
    "\n\n# DAN material\n"
    "Already searched for this message: nothing relevant was found. Don't "
    "cite DAN unless a search you run yourself returns something."
)


# "([DAN: Title](url))", with or without the outer parentheses
_DAN_LINK = re.compile(r"\s*\(?\[DAN: ([^\]]+)\]\(([^)\s]+)\)\)?")


def repair_dan_links(text: str, sources: list[dict[str, str]]) -> str:
    """Make every DAN citation point at an article that was really retrieved.

    Models sometimes mangle the URL. A citation whose title matches a
    retrieved article gets that article's URL; one that matches nothing is
    removed, so the answer never links to a DAN page nobody looked up.
    """
    by_url = {s["url"].rstrip("/"): s for s in sources}
    by_title = {s["title"].strip().lower(): s for s in sources}

    def fix(match: re.Match) -> str:
        title, url = match.group(1), match.group(2)
        source = by_url.get(url.rstrip("/")) or by_title.get(title.strip().lower())
        if source is None:
            return ""
        return f" ([DAN: {source['title']}]({source['url']}))"

    return _DAN_LINK.sub(fix, text)


def _hit_output_cap(response: types.GenerateContentResponse) -> bool:
    candidates = response.candidates or []
    return bool(candidates) and (
        candidates[0].finish_reason == types.FinishReason.MAX_TOKENS
    )


def _dive_line(row) -> str:
    site = str(row.get("dive_site_name", "N/A"))
    trip = str(row.get("trip_name", ""))
    location = site if site and site != "N/A" else "unknown"
    if trip and trip != "N/A" and trip != site:
        location += f" ({trip})"

    parts = [
        f"depth {row['max_depth']:.1f}m",
    ]
    if measured(row.get("max_ascend_speed")):
        parts.append(f"ascent {row['max_ascend_speed']:.1f}m/min")
    if measured(row.get("max_shallow_ascend_speed")):
        parts.append(f"surfacing {row['max_shallow_ascend_speed']:.1f}m/min")
    if row.get("entered_deco"):
        parts.append("ENTERED DECO")
    if measured(row.get("min_ndl")):
        parts.append(f"NDL {row['min_ndl']:.0f}min")
    if measured(row.get("sac_rate")):
        parts.append(f"SAC {row['sac_rate']:.1f}L/min")
    if measured(row.get("avg_temp")):
        temp_str = f"temp {row['avg_temp']:.1f}°C"
        grad = row.get("temp_gradient")
        if measured(grad) and grad > 1:
            temp_str += f" (gradient {grad:.1f}°C)"
        parts.append(temp_str)
    return f"  #{row['dive_number']} {location}: " + ", ".join(parts)


class DiverRoastAgent:
    """Manages conversation state and tool dispatch for the diver roasting agent."""

    def __init__(self):
        self._client = None
        self.history: list[types.Content] = []
        self.dive_data: pd.DataFrame | None = None
        self.features: pd.DataFrame | None = None
        # Read-only public ID for the shared dashboard. Never the session ID.
        self.share_id: str = secrets.token_urlsafe(16)
        self.dashboard = None  # cached DashboardResponse for the current log
        self.roast_summary: str | None = None
        self.roast_prompt: str | None = None
        self.roast_sources: list[dict] = []
        self.last_prompt: str | None = None
        self.last_sources: list[dict[str, str]] = []
        # One turn at a time per session: a turn runs in a worker thread and
        # mutates the history. The dashboard lock keeps its Gemini summaries
        # to one build per log.
        self.turn_lock = asyncio.Lock()
        self.dashboard_lock = asyncio.Lock()

    @property
    def client(self):
        """Lazily initialize the Gemini client."""
        if self._client is None:
            self._client = get_client()
        return self._client

    def set_dive_data(self, df: pd.DataFrame):
        """Store parsed dive log data and pre-compute per-dive feature summaries.

        Seeds the conversation history with a compact summary table so the LLM
        can identify patterns, flag dangerous dives, and roast the diver without
        needing to call heavy analysis tools on the full raw data. Metrics the
        log didn't record are left out of each line, not filled in.
        """
        self.dive_data = df
        # A new log gets a new share link; an already-shared page keeps its log.
        self.share_id = secrets.token_urlsafe(16)
        self.dashboard = None
        self.roast_summary = None
        self.roast_prompt = None
        self.roast_sources = []
        dive_numbers = df["dive_number"].unique().tolist()

        features_df = extract_features(df)
        self.features = features_df

        dive_lines = [_dive_line(row) for _, row in features_df.iterrows()]

        n = len(features_df)
        temps = features_df["avg_temp"]
        tropical = int((temps > 24).sum())
        temperate = int(((temps >= 15) & (temps <= 24)).sum())
        cold = int((temps < 15).sum())
        temp_exposure_parts = []
        if tropical:
            temp_exposure_parts.append(f"{tropical} tropical (>24°C)")
        if temperate:
            temp_exposure_parts.append(f"{temperate} temperate (15-24°C)")
        if cold:
            temp_exposure_parts.append(f"{cold} cold (<15°C)")
        temp_exposure_str = (
            ", ".join(temp_exposure_parts) if temp_exposure_parts else "unknown"
        )

        sac = features_df["sac_rate"]
        ndl = features_df["min_ndl"]
        agg = (
            f"Aggregates ({n} dives): "
            f"avg max depth {features_df['max_depth'].mean():.1f}m, "
            f"deepest {features_df['max_depth'].max():.1f}m, "
            f"avg SAC {fmt(sac.mean(), unit=' L/min')}, "
            f"worst SAC {fmt(sac.max(), unit=' L/min')}, "
            f"avg max ascent {fmt(features_df['max_ascend_speed'].mean(), unit=' m/min')}, "
            f"fastest ascent {fmt(features_df['max_ascend_speed'].max(), unit=' m/min')} "
            f"(30 s sustained), "
            f"{int((features_df['max_shallow_ascend_speed'] > 10).sum())} dives bolted "
            f"to the surface (>10 m/min through the last 8 m), "
            f"lowest NDL {fmt(ndl.min(), '.0f', ' min')}, "
            f"{int(features_df['entered_deco'].sum())} dives entered deco | "
            f"temperature exposure: {temp_exposure_str}, "
            f"avg thermocline gradient {fmt(features_df['temp_gradient'].mean(), unit='°C')}"
        )

        # Cap at 200 dives in context to avoid token bloat
        dive_summary = "\n".join(dive_lines[:200])
        if len(dive_lines) > 200:
            dive_summary += f"\n  ... and {len(dive_lines) - 200} more dives"

        scope = (
            "This upload is a SINGLE dive (for example one Garmin FIT file). "
            "Evaluate this dive on its own: its profile, ascents, NDL and gas. "
            "Don't talk about habits or patterns across a log. "
            if len(dive_numbers) == 1
            else ""
        )
        context_msg = (
            f"[System: The diver has uploaded a dive log containing {len(dive_numbers)} dives. "
            f"{scope}"
            f"Pre-computed feature summaries are below — use these to identify patterns and "
            f"dangerous dives. You can still call analyze_dive_profile or get_dive_summary "
            f"for deeper analysis of specific dives if needed, but you already have the key "
            f"metrics for every dive. Do NOT ask the user to upload — it's already done. "
            f"When referencing dives, always use the site name, not just the number. "
            f"A metric missing from a dive's line was not recorded by the dive computer: "
            f"say so, never estimate it (ascent rates can't be measured on a dive "
            f"that stays in the top few metres). 'ascent' is the fastest 30-second sustained "
            f"ascent rate; 'surfacing' is the fastest approach to the surface through "
            f"the last 8 m, where the pressure change is largest. Both limits are "
            f"10 m/min.\n\n"
            f"{coverage_line(features_df)}\n\n"
            f"{agg}\n\nPer-dive summaries:\n{dive_summary}]"
        )
        self.history.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=context_msg)],
            )
        )
        self.history.append(
            types.Content(
                role="model",
                parts=[
                    types.Part.from_text(
                        text=f"Got it — {len(dive_numbers)} dives loaded with full feature summaries. I'm ready to analyze and roast."
                    )
                ],
            )
        )

    def get_dive_numbers(self) -> list[str]:
        """Return list of available dive numbers."""
        if self.dive_data is None:
            return []
        from src.tools.dive import dive_sort_key

        return sorted(
            (str(d) for d in self.dive_data["dive_number"].unique()),
            key=dive_sort_key,
        )

    def _build_anomaly_keywords(self) -> str:
        """Build RAG-enriching keywords from measured dive anomalies."""
        return build_anomaly_keywords(getattr(self, "features", None))

    def _prior_search(self, user_message: str) -> str:
        """Search DAN before the model answers; return a system-prompt section.

        Grounding shouldn't depend on the model choosing to call a search
        tool: cheaper models answer without one. Searches the message itself
        and each anomaly measured in the log, as separate queries. The result
        is for this turn only and is not written to the history.
        """
        queries = [user_message, *anomaly_queries(getattr(self, "features", None))]
        try:
            found = dan.search_dan(queries, top_k=PRIOR_SEARCH_TOP_K)
        except Exception:
            logger.warning(
                "Prior DAN search failed; answering without it", exc_info=True
            )
            return ""
        if not found.sources:
            return NO_DAN_MATERIAL_NOTE
        self.last_sources.extend(found.sources)
        return DAN_MATERIAL_NOTE.format(material=found.text)

    def _execute_tool(self, function_call: types.FunctionCall) -> str:
        """Execute a tool function call and return the result text."""
        tracer = get_tracer()
        with tracer.start_as_current_span(
            f"tool.{function_call.name or 'unknown'}",
            attributes={"openinference.span.kind": "TOOL"},
        ) as span:
            func_name: str = function_call.name or ""
            args: dict[str, Any] = (
                dict(function_call.args) if function_call.args else {}
            )

            if func_name in DIVE_DATA_TOOLS:
                if self.dive_data is None:
                    return "No dive data loaded."
                args["dive_data"] = self.dive_data
                args["features"] = self.features

            # Search queries go out as the model wrote them. The log's
            # anomalies are searched separately before every answer
            # (_prior_search); appended here they swamped the model's query.

            func = TOOL_FUNCTIONS.get(func_name)
            if func is None:
                return f"Unknown tool: {func_name}"

            try:
                result = func(**args)
            except Exception as e:
                logger.exception("Tool %s failed (args: %s)", func_name, list(args))
                span.set_attribute("tool.error", True)
                span.record_exception(e)
                return f"Tool error ({func_name}): {e!s}"

            if isinstance(result, Retrieval):
                for source in result.sources:
                    if source not in self.last_sources:
                        self.last_sources.append(source)
                return result.text
            return result

    def _extract_function_calls(
        self, response: types.GenerateContentResponse
    ) -> list[types.FunctionCall]:
        """Extract function calls from a Gemini response, if any."""
        if not response.candidates:
            return []
        candidate = response.candidates[0]
        if not candidate.content or not candidate.content.parts:
            return []
        return [
            p.function_call
            for p in candidate.content.parts
            if p.function_call is not None
        ]

    @staticmethod
    def _describe_prompt(prompt_ver: PromptVersion) -> str:
        label = f"{prompt_ver.label} (v{prompt_ver.version})"
        if prompt_ver.phoenix_version_id:
            label += f" phoenix:{prompt_ver.phoenix_version_id}"
        return label

    def _run_turn(self, user_message: str, prompt_ver: PromptVersion) -> str:
        """Run one user turn through the bounded function-calling loop.

        DAN is searched first and the material added to the system prompt.
        At most ``AGENT_MAX_STEPS`` tool rounds; after that the model is
        forced to answer with tools disabled. The first round (choosing
        tools) runs at ``AGENT_TOOL_TEMPERATURE``, later rounds (usually the
        written answer) at ``AGENT_TEMPERATURE``.
        """
        self.last_sources = []
        self.last_prompt = self._describe_prompt(prompt_ver)
        system_instruction = prompt_ver.prompt + self._prior_search(user_message)
        self.history.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_message)],
            )
        )
        tools = [types.Tool(function_declarations=TOOL_DECLARATIONS)]

        for step in range(settings.AGENT_MAX_STEPS + 1):
            out_of_steps = step == settings.AGENT_MAX_STEPS
            if out_of_steps:
                logger.warning(
                    "Agent hit AGENT_MAX_STEPS=%d; forcing a text answer",
                    settings.AGENT_MAX_STEPS,
                )
                self.history.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=FORCE_ANSWER_NOTE)],
                    )
                )
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=tools,
                temperature=settings.AGENT_TOOL_TEMPERATURE
                if step == 0
                else settings.AGENT_TEMPERATURE,
                max_output_tokens=settings.CHAT_MAX_OUTPUT_TOKENS,
            )
            if out_of_steps:
                config.tool_config = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.NONE
                    )
                )
            response = self.client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=self.history,
                config=config,
            )
            if _hit_output_cap(response):
                logger.warning(
                    "Answer cut at CHAT_MAX_OUTPUT_TOKENS=%d",
                    settings.CHAT_MAX_OUTPUT_TOKENS,
                )

            function_calls = self._extract_function_calls(response)
            if function_calls and not out_of_steps:
                self.history.append(response.candidates[0].content)  # type: ignore[arg-type]
                tool_response_parts = [
                    types.Part.from_function_response(
                        name=fc.name or "",
                        response={"result": self._execute_tool(fc)},
                    )
                    for fc in function_calls
                ]
                self.history.append(
                    types.Content(role="user", parts=tool_response_parts)
                )
                continue

            text = repair_dan_links(response.text or "", self.last_sources)
            if text:
                self.history.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=text)],
                    )
                )
            return text
        return ""  # unreachable: the last step always returns

    def _span_attrs(self, prompt_ver: PromptVersion) -> dict:
        attrs = {
            "openinference.span.kind": "CHAIN",
            "prompt.version": prompt_ver.version,
            "prompt.label": prompt_ver.label,
        }
        if prompt_ver.phoenix_version_id:
            attrs["prompt.phoenix_version_id"] = prompt_ver.phoenix_version_id
        return attrs

    async def chat(self, user_message: str) -> AsyncGenerator[str, None]:
        """Process a user message and yield the final response text."""
        tracer = get_tracer()
        prompt_ver = get_active_prompt()
        with (
            tracer.start_as_current_span(
                "agent.chat", attributes=self._span_attrs(prompt_ver)
            ),
            using_attributes(session_id=str(id(self))),
        ):
            async with self.turn_lock:
                text = await asyncio.to_thread(self._run_turn, user_message, prompt_ver)
            if text:
                yield text

    async def chat_stream(self, user_message: str) -> AsyncGenerator[str, None]:
        """Yield the final response in chunks for SSE.

        Not token streaming: the answer is generated in full, then chunked.
        If an error occurs mid-way through tool calling, the conversation
        history is rolled back so subsequent messages don't see a broken state.
        """
        tracer = get_tracer()
        prompt_ver = get_active_prompt()
        with (
            tracer.start_as_current_span(
                "agent.chat_stream", attributes=self._span_attrs(prompt_ver)
            ),
            using_attributes(session_id=str(id(self))),
        ):
            async with self.turn_lock:
                history_snapshot = len(self.history)
                try:
                    # Model calls and the DAN search block for seconds; in a
                    # thread they don't stall every other request.
                    text = await asyncio.to_thread(
                        self._run_turn, user_message, prompt_ver
                    )
                except Exception:
                    self.history = self.history[:history_snapshot]
                    raise
            chunk_size = 20
            for i in range(0, len(text), chunk_size):
                yield text[i : i + chunk_size]
