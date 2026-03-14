import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from src.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _require_admin(x_admin_secret: str | None) -> None:
    """Raise 403 if the provided secret doesn't match settings.ADMIN_SECRET."""
    if not settings.ADMIN_SECRET or x_admin_secret != settings.ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


def _run_ingestion(full_replace: bool = False) -> None:
    """Run the DAN ingestion pipeline synchronously (called in a thread)."""
    from src.pipelines.dan_ingestion import run_pipeline

    logger.info(
        "RAG refresh started via admin endpoint (full_replace=%s)", full_replace
    )
    try:
        table = run_pipeline(full_replace=full_replace)
        logger.info("RAG refresh complete. Table: %s", table)
    except Exception:
        logger.exception("RAG refresh failed")


@router.post("/api/admin/refresh-rag")
async def refresh_rag(
    background_tasks: BackgroundTasks,
    x_admin_secret: str | None = Header(default=None),
    full_replace: bool = False,
):
    """Trigger a DAN RAG data refresh in the background.

    Protected by the X-Admin-Secret header. Set ADMIN_SECRET in the environment.
    Returns immediately — the pipeline runs asynchronously.

    Args:
        full_replace: Pass ?full_replace=true to drop and rebuild the entire
            table (use once to migrate an existing deployment to the chunk_id
            schema). Default is incremental merge.
    """
    _require_admin(x_admin_secret)

    loop = asyncio.get_event_loop()
    background_tasks.add_task(loop.run_in_executor, None, _run_ingestion, full_replace)

    mode = "full rebuild" if full_replace else "incremental merge"
    return {
        "status": "accepted",
        "message": f"RAG refresh ({mode}) started in background. Check logs for progress.",
    }
