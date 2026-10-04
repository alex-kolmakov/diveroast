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


# --- Per-IP rate limits -----------------------------------------------------


def test_sliding_window_limits_and_recovers():
    from src.api.limits import SlidingWindowLimiter

    limiter = SlidingWindowLimiter(limit=2, window=60)
    assert limiter.hit("a", now=0) is None
    assert limiter.hit("a", now=10) is None
    assert limiter.hit("a", now=20) == 40  # wait until the first hit expires
    assert limiter.hit("b", now=20) is None  # other clients unaffected
    assert limiter.hit("a", now=60) is None


def test_limiter_memory_is_bounded():
    from src.api.limits import SlidingWindowLimiter

    limiter = SlidingWindowLimiter(limit=1, window=60, max_keys=100)
    for i in range(1000):
        limiter.hit(f"ip{i}", now=0)
    assert len(limiter._hits) <= 100


@pytest.mark.anyio
async def test_uploads_are_rate_limited_per_ip(monkeypatch):
    from src.api import limits

    monkeypatch.setattr(limits.upload_limiter, "limit", 2)
    transport = ASGITransport(app=app)
    files = {"file": ("x.csv", b"a,b", "text/csv")}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        codes = [
            (await client.post("/api/upload", files=files)).status_code
            for _ in range(3)
        ]
        limited = await client.post("/api/upload", files=files)
    assert codes == [400, 400, 429]  # bad file type, but each attempt counts
    assert "Retry-After" in limited.headers
    assert "Too many uploads" in limited.json()["detail"]


@pytest.mark.anyio
async def test_chat_is_rate_limited_per_ip(monkeypatch):
    from src.api import limits

    monkeypatch.setattr(limits.chat_limiter, "limit", 1)
    transport = ASGITransport(app=app)
    body = {"message": "hi", "session_id": "nope"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/api/chat", json=body)
        second = await client.post("/api/chat", json=body)
    assert (first.status_code, second.status_code) == (404, 429)


def test_proxy_header_is_trusted_only_when_configured(monkeypatch):
    from starlette.requests import Request

    from src.api.limits import client_ip

    request = Request(
        {
            "type": "http",
            "headers": [(b"x-real-ip", b"203.0.113.9")],
            "client": ("10.0.0.2", 1234),
        }
    )
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    assert client_ip(request) == "10.0.0.2"
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    assert client_ip(request) == "203.0.113.9"


# --- Token usage and session budget -----------------------------------------


def _answer_with_usage(prompt_tokens: int, out_tokens: int, cached: int = 0):
    from google.genai import types

    client = _answer()
    client.models.generate_content.return_value.usage_metadata = (
        types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=out_tokens,
            thoughts_token_count=5,
            cached_content_token_count=cached,
        )
    )
    return client


def test_turn_tokens_are_counted_and_logged(caplog):
    import logging

    agent = DiverRoastAgent()
    agent._client = _answer_with_usage(1000, 50, cached=800)
    with (
        caplog.at_level(logging.INFO, logger="src.agent.usage"),
        patch.object(agent, "_prior_search", return_value=""),
    ):
        agent._run_turn("roast me", PROMPT)
    assert agent.messages_sent == 1
    assert agent.tokens_used == 1055  # input + output + thinking
    assert "1000 in (800 cached), 55 out" in caplog.text


def test_session_budget(monkeypatch):
    agent = DiverRoastAgent()
    assert agent.over_budget() is None
    agent.messages_sent = settings.SESSION_MAX_MESSAGES
    assert agent.over_budget() == "messages"
    agent.messages_sent = 0
    agent.tokens_used = settings.SESSION_MAX_TOKENS
    assert agent.over_budget() == "tokens"


@pytest.mark.anyio
async def test_chat_stops_when_the_session_budget_is_spent():
    sid, agent = deps.get_or_create_session()
    agent.tokens_used = settings.SESSION_MAX_TOKENS
    transport = ASGITransport(app=app)
    with patch.object(agent, "_run_turn") as turn:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/chat", json={"message": "hi", "session_id": sid}
            )
    assert r.status_code == 429
    assert "Upload your log again" in r.json()["detail"]
    turn.assert_not_called()


def test_local_client_reports_usage():
    from src.agent.openai_compat import OpenAICompatClient
    from src.agent.usage import usage_of

    local = OpenAICompatClient("http://local/v1", "k", "qwen")
    reply = MagicMock()
    reply.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }
    with patch.object(local.models._http, "post", return_value=reply):
        response = local.models.generate_content(model="x", contents="hi")
    assert usage_of(response).total == 150


# --- Daily budget -----------------------------------------------------------


def test_daily_budget_resets_at_midnight_utc(monkeypatch):
    from datetime import date

    from src.agent.usage import DailyBudget

    monkeypatch.setattr(settings, "DAILY_MAX_TOKENS", 100)
    budget = DailyBudget()
    budget.add(60, today=date(2026, 10, 20))
    assert not budget.spent(today=date(2026, 10, 20))
    budget.add(40, today=date(2026, 10, 20))
    assert budget.spent(today=date(2026, 10, 20))
    assert not budget.spent(today=date(2026, 10, 21))


def test_every_model_call_counts_against_the_daily_budget(monkeypatch):
    from src.agent.usage import daily_budget

    monkeypatch.setattr(settings, "DAILY_MAX_TOKENS", 1000)
    agent = DiverRoastAgent()
    agent._client = _answer_with_usage(990, 5)
    with patch.object(agent, "_prior_search", return_value=""):
        agent._run_turn("roast me", PROMPT)
    assert daily_budget.spent()


@pytest.mark.anyio
async def test_spent_daily_budget_pauses_chat_and_llm_summaries(monkeypatch):
    from src.agent.usage import daily_budget
    from src.api.routes import dashboard

    monkeypatch.setattr(settings, "DAILY_MAX_TOKENS", 10)
    daily_budget.add(10)
    sid, agent = deps.get_or_create_session()
    transport = ASGITransport(app=app)
    with patch.object(agent, "_run_turn") as turn:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/chat", json={"message": "hi", "session_id": sid}
            )
    assert r.status_code == 503
    assert "budget" in r.json()["detail"]
    turn.assert_not_called()

    dive = {
        "dive_number": "7",
        "site": "Reef",
        "pick_reason": "Fastest ascent",
        "issues": ["fast ascent"],
        "stats": {"max_depth": 20.0},
    }
    with patch.object(dashboard, "get_client") as get_client:
        summaries = dashboard._generate_dive_summaries([dive])
    get_client.assert_not_called()
    assert summaries == ["Dive #7 at Reef was flagged for fast ascent."]


# --- Rate-limited model -----------------------------------------------------


def _quota_error():
    from google.genai import errors

    return errors.ClientError(
        429,
        {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}},
    )


def test_model_429_is_retried_then_succeeds(monkeypatch):
    from src.agent.gemini_client import generate

    monkeypatch.setattr(settings, "MODEL_RETRY_BASE_SECONDS", 0)
    client = _answer()
    ok = client.models.generate_content.return_value
    client.models.generate_content.side_effect = [_quota_error(), ok]
    assert generate(client, contents="hi") is ok
    assert client.models.generate_content.call_count == 2


def test_model_429_gives_up_as_busy(monkeypatch):
    from src.agent.gemini_client import ModelBusyError, generate

    monkeypatch.setattr(settings, "MODEL_RETRY_BASE_SECONDS", 0)
    client = MagicMock()
    client.models.generate_content.side_effect = _quota_error()
    with pytest.raises(ModelBusyError):
        generate(client, contents="hi", attempts=3)
    assert client.models.generate_content.call_count == 3


def test_other_model_errors_are_not_retried():
    from google.genai import errors

    from src.agent.gemini_client import generate

    client = MagicMock()
    client.models.generate_content.side_effect = errors.ClientError(
        400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}}
    )
    with pytest.raises(errors.ClientError):
        generate(client, contents="hi")
    assert client.models.generate_content.call_count == 1


async def _chat_error(agent, sid) -> str:
    import json

    transport = ASGITransport(app=app)
    with patch("src.agent.conversation.get_active_prompt", return_value=PROMPT):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/chat", json={"message": "hi", "session_id": sid}
            )
    errors_sent = [
        json.loads(line[6:])["error"]
        for line in r.text.splitlines()
        if line.startswith("data: ") and '"error"' in line
    ]
    assert len(errors_sent) == 1
    return errors_sent[0]


@pytest.mark.anyio
async def test_busy_model_gets_a_friendly_message(monkeypatch):
    from src.api.routes.chat import MODEL_BUSY

    monkeypatch.setattr(settings, "MODEL_RETRY_BASE_SECONDS", 0)
    sid, agent = deps.get_or_create_session()
    agent._client = MagicMock()
    agent._client.models.generate_content.side_effect = _quota_error()
    with patch.object(agent, "_prior_search", return_value=""):
        assert await _chat_error(agent, sid) == MODEL_BUSY


@pytest.mark.anyio
async def test_internal_errors_are_not_sent_to_the_browser():
    from src.api.routes.chat import TURN_FAILED

    sid, agent = deps.get_or_create_session()
    secret = "GEMINI_API_KEY=abc123 at /app/src/agent/conversation.py"
    with patch.object(agent, "_run_turn", side_effect=RuntimeError(secret)):
        assert await _chat_error(agent, sid) == TURN_FAILED
