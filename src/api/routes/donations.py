import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response

from src.api.dependencies import get_donation_store
from src.api.models import DeleteDonationRequest
from src.storage.donations import DonationStore

router = APIRouter()


@router.post("/api/donations/delete", status_code=204)
async def delete_donation(
    request: DeleteDonationRequest,
    store: DonationStore = Depends(get_donation_store),
):
    """Delete a donated log with the deletion code shown after upload.

    The code goes in the body, not the URL, so it never lands in access logs.
    """
    if not await asyncio.to_thread(store.delete_with_code, request.code):
        raise HTTPException(
            status_code=404,
            detail="No donation matches that code. It may already be deleted.",
        )
    return Response(status_code=204)
