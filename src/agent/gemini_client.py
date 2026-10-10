import logging
import time

import httpx
from google import genai
from google.genai import errors, types

from src.agent.openai_compat import OpenAICompatClient
from src.config import settings

logger = logging.getLogger(__name__)

# Worth retrying: rate limit / quota, and the server being briefly unwell.
_RETRY_CODES = {429, 500, 502, 503, 504}


class ModelBusyError(Exception):
    """The model kept refusing (rate limit, quota, overload) after retries."""


def get_client() -> genai.Client | OpenAICompatClient:
    """Return the LLM client: Gemini, or a local server if LLM_BASE_URL is set."""
    if settings.LLM_BASE_URL:
        return OpenAICompatClient(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def thinking_config(level: str | None = None) -> types.ThinkingConfig | None:
    """A thinking level (default THINKING_LEVEL), or None for the model's own."""
    level = settings.THINKING_LEVEL if level is None else level
    if not level:
        return None
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel(level.upper()))


def _status(e: Exception) -> int | None:
    if isinstance(e, errors.APIError):
        return e.code
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code
    return None


def generate(
    client: genai.Client | OpenAICompatClient,
    *,
    contents: str | list[types.Content],
    config: types.GenerateContentConfig | None = None,
    attempts: int = 3,
) -> types.GenerateContentResponse:
    """``generate_content`` with backoff on rate limits and server errors.

    Blocks while it waits, so call it from a worker thread. Raises
    ModelBusyError once the retries are used up on a retryable error.
    """
    for attempt in range(attempts):
        started = time.monotonic()
        try:
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL, contents=contents, config=config
            )
            logger.info("Model answered in %.1f s", time.monotonic() - started)
            return response
        except Exception as e:
            code = _status(e)
            if code not in _RETRY_CODES:
                raise
            if attempt == attempts - 1:
                raise ModelBusyError(f"model returned {code}") from e
            delay = settings.MODEL_RETRY_BASE_SECONDS * 3**attempt
            logger.warning(
                "Model returned %s; retry %d/%d in %.0f s",
                code,
                attempt + 1,
                attempts - 1,
                delay,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")
