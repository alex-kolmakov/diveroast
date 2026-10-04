import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.api.dependencies import get_or_create_session
from src.api.limits import limit_uploads
from src.api.models import UploadResponse
from src.config import settings
from src.parsers import get_parser
from src.storage.sanitize import sanitize

router = APIRouter()
logger = logging.getLogger(__name__)


def _store_donation(content: bytes, filename: str) -> None:
    """Keep a donated log with people, notes and serials stripped.

    The raw upload is never written. The stored name is random: the
    original filename can carry a name.
    """
    try:
        clean = sanitize(content, filename)
    except Exception:
        logger.warning("Donation not stored: could not sanitize", exc_info=True)
        return
    donations_dir = Path(settings.DONATIONS_DIR)
    donations_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower()
    (donations_dir / f"{uuid.uuid4().hex}{ext}").write_bytes(clean)


@router.post(
    "/api/upload",
    response_model=UploadResponse,
    dependencies=[Depends(limit_uploads)],
)
async def upload_dive_log(
    file: UploadFile = File(...),
    session_id: str = Form(default=None),
    donate: bool = Form(default=False),
):
    """Upload a dive log file, parse it, and store in the session."""
    # Strip path separators from client-supplied filename to prevent path traversal
    filename = Path(file.filename or "upload.ssrf").name or "upload.ssrf"
    try:
        parser = get_parser(filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    # Write to temp file for parsing (50 MB limit matches nginx client_max_body_size)
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    suffix = os.path.splitext(filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Parsing and feature extraction take seconds on a big log; in a
        # thread they don't stall every other request.
        df = await asyncio.to_thread(parser.parse, tmp_path)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to parse file: {str(e)}"
        ) from None
    finally:
        os.unlink(tmp_path)

    if df.empty or "dive_number" not in df.columns:
        raise HTTPException(status_code=400, detail=f"No dives found in {filename}")

    if donate:
        await asyncio.to_thread(_store_donation, content, filename)

    sid, agent = get_or_create_session(session_id)
    await asyncio.to_thread(agent.set_dive_data, df)
    dive_numbers = agent.get_dive_numbers()

    return UploadResponse(
        session_id=sid,
        dive_count=len(dive_numbers),
        dive_numbers=[str(d) for d in dive_numbers],
        message=f"Successfully parsed {len(dive_numbers)} dives from {filename}",
    )
