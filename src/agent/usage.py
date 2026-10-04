"""Token accounting for model calls: logged, traced and counted for budgets."""

import logging
from dataclasses import dataclass

from google.genai import types
from opentelemetry import trace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0  # includes thinking tokens
    cached: int = 0  # part of input served from Gemini's implicit cache

    @property
    def total(self) -> int:
        return self.input + self.output


def usage_of(response: types.GenerateContentResponse) -> Usage:
    meta = response.usage_metadata
    if meta is None:
        return Usage()
    return Usage(
        input=meta.prompt_token_count or 0,
        output=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
        cached=meta.cached_content_token_count or 0,
    )


def record_usage(response: types.GenerateContentResponse, call: str) -> Usage:
    """Log and trace one model call's token counts; return them."""
    usage = usage_of(response)
    logger.info(
        "model call %s: %d in (%d cached), %d out",
        call,
        usage.input,
        usage.cached,
        usage.output,
    )
    span = trace.get_current_span()
    span.set_attribute("diveroast.tokens.input", usage.input)
    span.set_attribute("diveroast.tokens.cached", usage.cached)
    span.set_attribute("diveroast.tokens.output", usage.output)
    return usage
