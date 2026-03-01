from collections.abc import AsyncGenerator

import pandas as pd
from google.genai import types
from openinference.instrumentation import using_attributes

from src.agent.gemini_client import get_client
from src.agent.system_prompts import get_active_prompt
from src.agent.tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS
from src.analysis.feature_engineering import extract_features
from src.config import settings
from src.observability import get_tracer


class DiverRoastAgent:
    """Manages conversation state and tool dispatch for the diver roasting agent."""

    def __init__(self):
        self._client = None
        self.history: list[types.Content] = []
        self.dive_data: pd.DataFrame | None = None

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
        needing to call heavy analysis tools on the full raw data.
        """
        self.dive_data = df
        dive_numbers = sorted(df["dive_number"].unique().tolist())

        # Pre-compute per-dive features
        features_df = extract_features(df)
        self.features = features_df

        # Build compact per-dive summary lines
        dive_lines = []
        for _, row in features_df.iterrows():
            dn = row["dive_number"]
            site = str(row.get("dive_site_name", "N/A"))
            trip = str(row.get("trip_name", ""))
            location = site if site and site != "N/A" else "unknown"
            if trip and trip != "N/A" and trip != site:
                location += f" ({trip})"

            line = (
                f"  #{dn} {location}: "
                f"depth {row['max_depth']:.1f}m, "
                f"ascent {row['max_ascend_speed']:.1f}m/min, "
                f"NDL {row['min_ndl']:.0f}min, "
                f"SAC {row['sac_rate']:.1f}L/min, "
                f"temp {row['avg_temp']:.1f}°C"
            )
            if row.get("adverse_conditions"):
                line += " [ADVERSE]"
            dive_lines.append(line)

        # Compute aggregate stats
        n = len(features_df)
        agg = (
            f"Aggregates ({n} dives): "
            f"avg max depth {features_df['max_depth'].mean():.1f}m, "
            f"deepest {features_df['max_depth'].max():.1f}m, "
            f"avg SAC {features_df['sac_rate'].mean():.1f} L/min, "
            f"worst SAC {features_df['sac_rate'].max():.1f} L/min, "
            f"avg max ascent {features_df['max_ascend_speed'].mean():.1f} m/min, "
            f"fastest ascent {features_df['max_ascend_speed'].max():.1f} m/min, "
            f"lowest NDL {features_df['min_ndl'].min():.0f} min, "
            f"{int(features_df['adverse_conditions'].sum())} adverse-condition dives"
        )

        # Cap at 200 dives in context to avoid token bloat
        dive_summary = "\n".join(dive_lines[:200])
        if len(dive_lines) > 200:
            dive_summary += f"\n  ... and {len(dive_lines) - 200} more dives"

        context_msg = (
            f"[System: The diver has uploaded a dive log containing {len(dive_numbers)} dives. "
            f"Pre-computed feature summaries are below — use these to identify patterns and "
            f"dangerous dives. You can still call analyze_dive_profile or get_dive_summary "
            f"for deeper analysis of specific dives if needed, but you already have the key "
            f"metrics for every dive. Do NOT ask the user to upload — it's already done. "
            f"When referencing dives, always use the site name, not just the number.\n\n"
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
        return sorted(self.dive_data["dive_number"].unique().tolist())

    def _get_dive_data_json(self) -> str:
        """Serialize dive data to JSON for tool calls."""
        if self.dive_data is None:
            return "[]"
        return self.dive_data.to_json()

    def _build_anomaly_keywords(self) -> str:
        """Build RAG-enriching keywords from objective dive anomalies.

        Thresholds match analyze_dive_profile() for consistency.
        Note: adverse_conditions (rating < 3) is intentionally excluded —
        a star rating is subjective and would add noise to RAG queries.
        """
        if (
            not hasattr(self, "features")
            or self.features is None
            or self.features.empty
        ):
            return ""
        keywords: list[str] = []
        f = self.features
        if f["max_ascend_speed"].max() > 10:
            keywords.append("rapid ascent decompression sickness")
        if f["min_ndl"].min() < 1:
            keywords.append("close to deco stop NDL almost zero recreational limit")
        if f["sac_rate"].max() > 20:
            keywords.append("high air consumption SAC rate breathing")
        if f["max_depth"].max() > 30:
            keywords.append("deep diving incident")
        return " ".join(keywords)

    def _execute_tool(self, function_call: types.FunctionCall) -> str:
        """Execute a tool function call and return the result."""
        tracer = get_tracer()
        with tracer.start_as_current_span(
            f"tool.{function_call.name or 'unknown'}",
            attributes={"openinference.span.kind": "TOOL"},
        ):
            func_name: str = function_call.name or ""
            args = dict(function_call.args) if function_call.args else {}

            # Inject dive_data_json for tools that need it
            if func_name in (
                "analyze_dive_profile",
                "get_dive_summary",
                "list_dives",
                "analyze_all_dives",
            ):
                args["dive_data_json"] = self._get_dive_data_json()

            # Augment RAG queries with objective anomaly keywords so the diver's
            # actual safety issues surface even when their question is generic.
            if func_name in ("search_dan_incidents", "search_dan_guidelines"):
                anomaly_keywords = self._build_anomaly_keywords()
                if anomaly_keywords:
                    args["query"] = (
                        f"{args.get('query', '')} {anomaly_keywords}".strip()
                    )

            func = TOOL_FUNCTIONS.get(func_name)
            if func is None:
                return f"Unknown tool: {func_name}"

            try:
                return func(**args)
            except Exception as e:
                return f"Tool error ({func_name}): {e!s}"

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

    async def chat(self, user_message: str) -> AsyncGenerator[str, None]:
        """Process a user message and yield streaming response chunks.

        Handles the Gemini function-calling loop: call -> tool -> call -> final text.
        """
        tracer = get_tracer()
        prompt_ver = get_active_prompt()
        span_attrs = {
            "openinference.span.kind": "CHAIN",
            "prompt.version": prompt_ver.version,
            "prompt.label": prompt_ver.label,
        }
        if prompt_ver.phoenix_version_id:
            span_attrs["prompt.phoenix_version_id"] = prompt_ver.phoenix_version_id
        with (
            tracer.start_as_current_span(
                "agent.chat",
                attributes=span_attrs,
            ),
            using_attributes(session_id=str(id(self))),
        ):
            self.history.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=user_message)],
                )
            )

            tools = [types.Tool(function_declarations=TOOL_DECLARATIONS)]

            while True:
                response = self.client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=self.history,
                    config=types.GenerateContentConfig(
                        system_instruction=prompt_ver.prompt,
                        tools=tools,
                        temperature=0.8,
                    ),
                )

                function_calls = self._extract_function_calls(response)

                if function_calls:
                    # Add the model's response (with tool calls) to history
                    self.history.append(response.candidates[0].content)  # type: ignore[arg-type]

                    # Execute all tool calls and build response parts
                    tool_response_parts = []
                    for fc in function_calls:
                        result = self._execute_tool(fc)
                        tool_response_parts.append(
                            types.Part.from_function_response(
                                name=fc.name or "",
                                response={"result": result},
                            )
                        )

                    # Add tool results to history
                    self.history.append(
                        types.Content(role="user", parts=tool_response_parts)
                    )
                    # Continue the loop — model will process tool results
                    continue

                # No tool calls — extract final text response
                if response.text:
                    self.history.append(
                        types.Content(
                            role="model",
                            parts=[types.Part.from_text(text=response.text)],
                        )
                    )
                    yield response.text
                break

    async def chat_stream(self, user_message: str) -> AsyncGenerator[str, None]:
        """Stream response token-by-token using Gemini's streaming API.

        Falls back to non-streaming for tool-calling rounds, then streams
        the final text response.  If an error occurs mid-way through tool
        calling, the conversation history is rolled back so subsequent
        messages don't see a broken state.
        """
        tracer = get_tracer()
        prompt_ver = get_active_prompt()
        stream_span_attrs = {
            "openinference.span.kind": "CHAIN",
            "prompt.version": prompt_ver.version,
            "prompt.label": prompt_ver.label,
        }
        if prompt_ver.phoenix_version_id:
            stream_span_attrs["prompt.phoenix_version_id"] = (
                prompt_ver.phoenix_version_id
            )
        with (
            tracer.start_as_current_span(
                "agent.chat_stream",
                attributes=stream_span_attrs,
            ),
            using_attributes(session_id=str(id(self))),
        ):
            # Snapshot history length so we can roll back on failure
            history_snapshot = len(self.history)

            self.history.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=user_message)],
                )
            )

            tools = [types.Tool(function_declarations=TOOL_DECLARATIONS)]

            try:
                while True:
                    response = self.client.models.generate_content(
                        model=settings.GEMINI_MODEL,
                        contents=self.history,
                        config=types.GenerateContentConfig(
                            system_instruction=prompt_ver.prompt,
                            tools=tools,
                            temperature=0.8,
                        ),
                    )

                    function_calls = self._extract_function_calls(response)

                    if function_calls:
                        self.history.append(response.candidates[0].content)  # type: ignore[arg-type]

                        tool_response_parts = []
                        for fc in function_calls:
                            result = self._execute_tool(fc)
                            tool_response_parts.append(
                                types.Part.from_function_response(
                                    name=fc.name or "",
                                    response={"result": result},
                                )
                            )

                        self.history.append(
                            types.Content(role="user", parts=tool_response_parts)
                        )
                        continue

                    # No tool calls — yield the final text response
                    if response.text:
                        self.history.append(
                            types.Content(
                                role="model",
                                parts=[types.Part.from_text(text=response.text)],
                            )
                        )
                        # Yield in chunks for SSE streaming
                        text = response.text
                        chunk_size = 20
                        for i in range(0, len(text), chunk_size):
                            yield text[i : i + chunk_size]
                    break
            except Exception:
                # Roll back history to before this message so the agent
                # state stays clean for the next user message
                self.history = self.history[:history_snapshot]
                raise
