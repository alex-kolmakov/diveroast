from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.dependencies import get_snapshot_store
from src.api.models import DashboardResponse
from src.storage.snapshots import SnapshotStore

router = APIRouter()


@router.get("/api/shared/{share_id}", response_model=DashboardResponse)
async def get_shared_dashboard(
    share_id: str,
    store: SnapshotStore = Depends(get_snapshot_store),
):
    """Return a previously saved dashboard snapshot by share ID.

    This endpoint is public and requires no session — it just fetches the
    pre-computed DashboardResponse that was persisted when the original user
    loaded their dashboard.
    """
    data = await store.load(share_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="Shared results not found. The link may be invalid or the results may have expired.",
        )
    return data


class RoastUpdateRequest(BaseModel):
    roast: str


@router.patch("/api/sessions/{session_id}/roast")
async def save_roast_summary(
    session_id: str,
    body: RoastUpdateRequest,
    store: SnapshotStore = Depends(get_snapshot_store),
):
    """Persist the auto-generated roast text into the session snapshot so shared views can display it."""
    await store.update_roast(session_id, body.roast)
    return {"ok": True}
