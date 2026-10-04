"""Guards for running in public: concurrency, cost caps, rate limits."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import src.api.dependencies as deps
from src.agent.conversation import DiverRoastAgent
from src.api.main import app
from src.config import settings

PROMPT = MagicMock(prompt="p", label="test", version=0, phoenix_version_id=None)


def _slow_turn(seconds: float, text: str = "Slow down."):
    def run(user_message, prompt_ver):
        time.sleep(seconds)
        return text

    return run


async def test_a_turn_does_not_block_the_event_loop():
    """A roast takes seconds; other requests must keep being served."""
    agent = DiverRoastAgent()
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    with (
        patch.object(agent, "_run_turn", side_effect=_slow_turn(0.3)),
        patch("src.agent.conversation.get_active_prompt", return_value=PROMPT),
    ):
        task = asyncio.create_task(ticker())
        chunks = [c async for c in agent.chat_stream("roast me")]
        task.cancel()

    assert "".join(chunks) == "Slow down."
    assert ticks >= 5


@pytest.mark.anyio
async def test_second_message_while_answering_is_rejected():
    sid, agent = deps.get_or_create_session()
    transport = ASGITransport(app=app)
    with (
        patch.object(agent, "_run_turn", side_effect=_slow_turn(0.3)),
        patch("src.agent.conversation.get_active_prompt", return_value=PROMPT),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(
                client.post("/api/chat", json={"message": "a", "session_id": sid})
            )
            await asyncio.sleep(0.1)
            second = await client.post(
                "/api/chat", json={"message": "b", "session_id": sid}
            )
            assert second.status_code == 409
            assert (await first).status_code == 200


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- Output caps ------------------------------------------------------------


def _answer(text="Slow down.", finish_reason=None):
    from google.genai import types

    client = MagicMock()
    client.models.generate_content.return_value = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                finish_reason=finish_reason,
            )
        ]
    )
    return client


def test_chat_calls_cap_output_tokens(caplog):
    from google.genai import types

    agent = DiverRoastAgent()
    agent._client = _answer(finish_reason=types.FinishReason.MAX_TOKENS)
    with patch.object(agent, "_prior_search", return_value=""):
        agent._run_turn("roast me", PROMPT)
    config = agent._client.models.generate_content.call_args.kwargs["config"]
    assert config.max_output_tokens == settings.CHAT_MAX_OUTPUT_TOKENS
    assert "CHAT_MAX_OUTPUT_TOKENS" in caplog.text


def test_dive_summaries_cap_output_tokens():
    from src.api.routes import dashboard

    client = _answer('["Too fast."]')
    dive = {
        "dive_number": "1",
        "site": "Reef",
        "pick_reason": "Fastest ascent",
        "issues": ["fast ascent"],
        "stats": {"max_depth": 20.0},
    }
    with patch.object(dashboard, "get_client", return_value=client):
        assert dashboard._generate_dive_summaries([dive]) == ["Too fast."]
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.max_output_tokens == settings.SUMMARY_MAX_OUTPUT_TOKENS


def test_local_client_passes_the_output_cap():
    from google.genai import types

    from src.agent.openai_compat import OpenAICompatClient

    local = OpenAICompatClient("http://local/v1", "k", "qwen")
    reply = MagicMock()
    reply.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    with patch.object(local.models._http, "post", return_value=reply) as post:
        local.models.generate_content(
            model="x",
            contents="hi",
            config=types.GenerateContentConfig(max_output_tokens=64),
        )
    assert post.call_args.kwargs["json"]["max_tokens"] == 64


# --- Message length ---------------------------------------------------------


@pytest.mark.anyio
async def test_oversized_chat_message_never_reaches_the_model():
    from src.api.models import CHAT_MESSAGE_MAX_CHARS

    sid, agent = deps.get_or_create_session()
    transport = ASGITransport(app=app)
    with patch.object(agent, "_run_turn") as turn:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            too_long = "a" * (CHAT_MESSAGE_MAX_CHARS + 1)
            r = await client.post(
                "/api/chat", json={"message": too_long, "session_id": sid}
            )
            empty = await client.post(
                "/api/chat", json={"message": "", "session_id": sid}
            )
    assert r.status_code == 422
    assert empty.status_code == 422
    turn.assert_not_called()
