import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.agent.conversation import DiverRoastAgent
from src.agent.gemini_client import ModelBusyError
from src.agent.usage import daily_budget
from src.api.dependencies import get_session, get_snapshot_store
from src.api.limits import limit_chats
from src.api.models import ChatRequest, Source
from src.storage.snapshots import SnapshotStore

router = APIRouter()
logger = logging.getLogger(__name__)

MODEL_BUSY = (
    "DiveRoast is roasting a lot of divers right now. Give it a minute and "
    "send your message again."
)
TURN_FAILED = "Something went wrong writing that answer. Try sending it again."


def _mark_cited(sources: list[dict[str, str]], text: str) -> list[dict]:
    """Flag the retrieved articles the answer actually links to.

    Retrieval returns whatever scored highest; only a link in the text shows
    the answer used it.
    """
    return [{**s, "cited": s["url"].rstrip("/") in text} for s in sources]


async def _record_roast(
    agent: DiverRoastAgent, text: str, sources: list[dict], store: SnapshotStore
) -> None:
    """Keep the first answer after an upload as the log's roast.

    Saved server-side into the shared snapshot, so no client call can
    rewrite the public page.
    """
    if agent.roast_summary is not None or not text or agent.dive_data is None:
        return
    agent.roast_summary = text
    agent.roast_prompt = agent.last_prompt
    agent.roast_sources = sources
    if agent.dashboard is not None:
        agent.dashboard = agent.dashboard.model_copy(
            update={
                "roast_summary": text,
                "roast_prompt": agent.roast_prompt,
                "roast_sources": [Source(**s) for s in sources],
            }
        )
        await store.save(
            agent.share_id, agent.dashboard.model_copy(update={"session_id": None})
        )


@router.post("/api/chat", dependencies=[Depends(limit_chats)])
async def chat(
    request: ChatRequest,
    store: SnapshotStore = Depends(get_snapshot_store),
):
    """Send a message and receive a streaming SSE response.

    Requires an existing session (created by /api/upload). Emits ``message``
    chunks, then ``sources`` (DAN articles retrieved this turn, if any), then
    ``done``.
    """
    found = get_session(request.session_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Session not found")
    agent: DiverRoastAgent = found
    if daily_budget.spent():
        raise HTTPException(
            status_code=503,
            detail="DiveRoast has used up today's roasting budget. Your dashboard "
            "still works; roasts are back after midnight UTC.",
        )
    if agent.over_budget():
        logger.info("Session hit its budget (%s)", agent.over_budget())
        raise HTTPException(
            status_code=429,
            detail="This chat has hit its limit. Upload your log again to start a new one.",
        )
    if agent.turn_lock.locked():
        raise HTTPException(
            status_code=409, detail="Still answering your last message."
        )

    async def event_generator():
        parts: list[str] = []
        try:
            async for chunk in agent.chat_stream(request.message):
                parts.append(chunk)
                yield {"event": "message", "data": json.dumps({"content": chunk})}
            text = "".join(parts)
            sources = _mark_cited(agent.last_sources, text)
            if sources:
                yield {"event": "sources", "data": json.dumps({"sources": sources})}
            await _record_roast(agent, text, sources, store)
            yield {"event": "done", "data": json.dumps({"status": "complete"})}
        except ModelBusyError:
            logger.warning("Chat turn gave up: model busy after retries")
            yield {"event": "error", "data": json.dumps({"error": MODEL_BUSY})}
        except Exception:
            # The details stay in the log; the browser gets no internals.
            logger.exception("Chat turn failed")
            yield {"event": "error", "data": json.dumps({"error": TURN_FAILED})}

    return EventSourceResponse(event_generator(), ping=15)
