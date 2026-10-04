import asyncio
import logging
import re
import time
from pathlib import Path

from src.api.models import DashboardResponse

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


class SnapshotStore:
    async def save(self, share_id: str, data: DashboardResponse) -> None:
        raise NotImplementedError

    async def load(self, share_id: str) -> DashboardResponse | None:
        raise NotImplementedError

    def purge(self, max_age_days: int, now: float | None = None) -> int:
        raise NotImplementedError


class LocalSnapshotStore(SnapshotStore):
    """Stores snapshots as JSON files on the local filesystem."""

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, share_id: str) -> Path:
        if not _SAFE_ID.match(share_id):
            raise ValueError(f"Invalid share_id: {share_id!r}")
        return self._dir / f"{share_id}.json"

    async def save(self, share_id: str, data: DashboardResponse) -> None:
        path = self._safe_path(share_id)
        await asyncio.to_thread(path.write_text, data.model_dump_json())
        logger.info("Snapshot saved: %s", path)

    def purge(self, max_age_days: int, now: float | None = None) -> int:
        """Delete snapshots last saved more than ``max_age_days`` ago."""
        cutoff = (time.time() if now is None else now) - max_age_days * 86400
        removed = 0
        for path in self._dir.glob("*.json"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    async def load(self, share_id: str) -> DashboardResponse | None:
        try:
            path = self._safe_path(share_id)
        except ValueError:
            return None
        try:
            raw = await asyncio.to_thread(path.read_text)
            return DashboardResponse.model_validate_json(raw)
        except FileNotFoundError:
            return None
        except Exception:
            logger.warning("Failed to load snapshot %s", share_id, exc_info=True)
            return None
