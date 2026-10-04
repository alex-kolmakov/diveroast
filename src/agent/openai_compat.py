"""Adapter for OpenAI-compatible servers (LM Studio, Ollama, llama.cpp).

Exposes the one slice of the google-genai client the app uses,
``client.models.generate_content(model=, contents=, config=)``, and returns
genai response types, so the agent loop and dashboard run unchanged against a
local model. Enabled by setting ``LLM_BASE_URL``.
"""

import json
import re
from typing import Any

import httpx
from google.genai import types

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _json_schema(schema: types.Schema | None) -> dict[str, Any]:
    """Convert a genai Schema to JSON Schema (genai type names are uppercase)."""
    if schema is None:
        return {"type": "object", "properties": {}}

    def lower_types(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: v.lower() if k == "type" and isinstance(v, str) else lower_types(v)
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [lower_types(v) for v in node]
        return node

    out = lower_types(schema.model_dump(exclude_none=True, mode="json"))
    out.setdefault("properties", {})
    return out


def _to_messages(
    contents: str | list[types.Content], system: Any
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": str(system)})
    if isinstance(contents, str):
        messages.append({"role": "user", "content": contents})
        return messages

    call_seq = 0
    pending_ids: list[str] = []  # tool calls awaiting their responses, in order
    for content in contents:
        role = "assistant" if content.role == "model" else "user"
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for part in content.parts or []:
            if part.function_call is not None:
                fc = part.function_call
                call_seq += 1
                call_id = fc.id or f"call_{call_seq}"
                pending_ids.append(call_id)
                tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": fc.name or "",
                            "arguments": json.dumps(dict(fc.args or {})),
                        },
                    }
                )
            elif part.function_response is not None:
                payload = part.function_response.response or {}
                result = payload.get("result", payload)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": pending_ids.pop(0) if pending_ids else "",
                        "content": result
                        if isinstance(result, str)
                        else json.dumps(result, default=str),
                    }
                )
            elif part.text:
                text_parts.append(part.text)
        if tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": "\n".join(text_parts),
                    "tool_calls": tool_calls,
                }
            )
        elif text_parts:
            messages.append({"role": role, "content": "\n".join(text_parts)})
    return messages


class _Models:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float):
        self._model = model
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def generate_content(
        self,
        *,
        model: str,  # noqa: ARG002 - the Gemini model name; the local one is used
        contents: str | list[types.Content],
        config: types.GenerateContentConfig | None = None,
    ) -> types.GenerateContentResponse:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": _to_messages(
                contents, config.system_instruction if config else None
            ),
        }
        if config is not None:
            if config.temperature is not None:
                body["temperature"] = config.temperature
            if config.max_output_tokens is not None:
                body["max_tokens"] = config.max_output_tokens
            declarations = [
                fd
                for tool in config.tools or []
                if isinstance(tool, types.Tool)
                for fd in tool.function_declarations or []
            ]
            if declarations:
                body["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": fd.name,
                            "description": fd.description or "",
                            "parameters": _json_schema(fd.parameters),
                        },
                    }
                    for fd in declarations
                ]
                tool_config = config.tool_config
                fcc = tool_config.function_calling_config if tool_config else None
                if fcc and fcc.mode == types.FunctionCallingConfigMode.NONE:
                    body["tool_choice"] = "none"

        resp = self._http.post("/chat/completions", json=body)
        resp.raise_for_status()
        message = resp.json()["choices"][0]["message"]

        parts: list[types.Part] = []
        text = _THINK_RE.sub("", message.get("content") or "").strip()
        if text:
            parts.append(types.Part.from_text(text=text))
        for call in message.get("tool_calls") or []:
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        id=call.get("id"),
                        name=fn.get("name", ""),
                        args=args if isinstance(args, dict) else {},
                    )
                )
            )
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(content=types.Content(role="model", parts=parts))
            ]
        )


class OpenAICompatClient:
    """Stand-in for ``genai.Client`` backed by an OpenAI-compatible server."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 600.0):
        self.models = _Models(base_url, api_key, model, timeout)
