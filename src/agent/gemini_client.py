from google import genai

from src.agent.openai_compat import OpenAICompatClient
from src.config import settings


def get_client() -> genai.Client | OpenAICompatClient:
    """Return the LLM client: Gemini, or a local server if LLM_BASE_URL is set."""
    if settings.LLM_BASE_URL:
        return OpenAICompatClient(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY)
