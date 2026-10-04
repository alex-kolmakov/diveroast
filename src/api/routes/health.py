import asyncio
import shutil
import time

import lancedb
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.agent.usage import daily_budget
from src.config import settings

router = APIRouter()

# Uptime checks hit this every minute; the row count is cached that long.
_CACHE_SECONDS = 60
_dan_rows: tuple[float, int | None] = (0.0, None)


def _count_dan_rows() -> int | None:
    """Rows in the DAN table, or None if it can't be opened."""
    try:
        db = lancedb.connect(settings.LANCEDB_URI)
        return db.open_table(settings.LANCEDB_TABLE_NAME).count_rows()
    except Exception:
        return None


async def _cached_dan_rows() -> int | None:
    global _dan_rows
    checked, rows = _dan_rows
    if time.monotonic() - checked > _CACHE_SECONDS:
        rows = await asyncio.to_thread(_count_dan_rows)
        _dan_rows = (time.monotonic(), rows)
    return rows


def _disk_free_mb() -> int:
    return shutil.disk_usage(settings.SNAPSHOT_DIR).free // (1024 * 1024)


@router.get("/health")
async def health():
    """503 when DiveRoast can't do its job: no DAN index (or a partial one,
    like the 322-row rebuild), or a disk about to fill. A spent daily budget
    is reported but isn't an outage: the dashboard still works.
    """
    rows = await _cached_dan_rows()
    try:
        disk_free = _disk_free_mb()
    except OSError:
        disk_free = None
    problems = []
    if rows is None:
        problems.append("DAN index can't be opened")
    elif rows < settings.RAG_MIN_ROWS:
        problems.append(f"DAN index has {rows} rows (< {settings.RAG_MIN_ROWS})")
    if disk_free is not None and disk_free < settings.HEALTH_MIN_DISK_FREE_MB:
        problems.append(f"{disk_free} MB disk free")
    body = {
        "status": "unhealthy" if problems else "healthy",
        "problems": problems,
        "dan_rows": rows,
        "disk_free_mb": disk_free,
        "roasts_paused": daily_budget.spent(),
    }
    return JSONResponse(body, status_code=503 if problems else 200)
