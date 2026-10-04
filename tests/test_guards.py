"""Guards for running in public: concurrency, cost caps, rate limits."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import src.api.dependencies as deps
from src.agent.conversation import DiverRoastAgent
from src.api.main import app

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
