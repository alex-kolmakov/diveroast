import asyncio
import logging
from pathlib import Path

from src.api.models import DashboardResponse

logger = logging.getLogger(__name__)


class SnapshotStore:
    async def save(self, share_id: str, data: DashboardResponse) -> None:
        raise NotImplementedError

    async def load(self, share_id: str) -> DashboardResponse | None:
        raise NotImplementedError

    async def update_roast(self, share_id: str, roast_text: str) -> None:
        data = await self.load(share_id)
        if data is None:
            logger.warning("Cannot update roast: snapshot %s not found", share_id)
            return
        data.roast_summary = roast_text
        await self.save(share_id, data)


class LocalSnapshotStore(SnapshotStore):
    """Stores snapshots as JSON files on the local filesystem."""

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    async def save(self, share_id: str, data: DashboardResponse) -> None:
        path = self._dir / f"{share_id}.json"
        await asyncio.to_thread(path.write_text, data.model_dump_json())
        logger.info("Snapshot saved: %s", path)

    async def load(self, share_id: str) -> DashboardResponse | None:
        path = self._dir / f"{share_id}.json"
        try:
            raw = await asyncio.to_thread(path.read_text)
            return DashboardResponse.model_validate_json(raw)
        except FileNotFoundError:
            return None
        except Exception:
            logger.warning("Failed to load snapshot %s", share_id, exc_info=True)
            return None
