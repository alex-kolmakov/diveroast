from fastapi import APIRouter, Depends, HTTPException

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

    This endpoint is public and read-only. The share ID is separate from the
    session ID, so it grants no access to chat, upload or the live
    dashboard. Snapshots never carry the session ID; the strip below covers
    snapshots saved before that was the case.
    """
    data = await store.load(share_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail="Shared results not found. The link may be invalid or the results may have expired.",
        )
    return data.model_copy(update={"session_id": None})
