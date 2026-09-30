import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.agent.conversation import DiverRoastAgent
from src.api.dependencies import get_session, get_snapshot_store
from src.api.models import ChatRequest
from src.storage.snapshots import SnapshotStore

router = APIRouter()
logger = logging.getLogger(__name__)


async def _record_roast(
    agent: DiverRoastAgent, text: str, store: SnapshotStore
) -> None:
    """Keep the first answer after an upload as the log's roast.

    Saved server-side into the shared snapshot, so no client call can
    rewrite the public page.
    """
    if agent.roast_summary is not None or not text or agent.dive_data is None:
        return
    agent.roast_summary = text
    agent.roast_prompt = agent.last_prompt
    if agent.dashboard is not None:
        agent.dashboard = agent.dashboard.model_copy(
            update={"roast_summary": text, "roast_prompt": agent.roast_prompt}
        )
        await store.save(
            agent.share_id, agent.dashboard.model_copy(update={"session_id": None})
        )


@router.post("/api/chat")
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

    async def event_generator():
        parts: list[str] = []
        try:
            async for chunk in agent.chat_stream(request.message):
                parts.append(chunk)
                yield {"event": "message", "data": json.dumps({"content": chunk})}
            if agent.last_sources:
                yield {
                    "event": "sources",
                    "data": json.dumps({"sources": agent.last_sources}),
                }
            await _record_roast(agent, "".join(parts), store)
            yield {"event": "done", "data": json.dumps({"status": "complete"})}
        except Exception as e:
            logger.exception("Chat turn failed")
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)}),
            }

    return EventSourceResponse(event_generator(), ping=15)
