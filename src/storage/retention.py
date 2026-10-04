"""Delete donations and shared links once they pass their retention period."""

import asyncio
import logging

from src.config import settings

logger = logging.getLogger(__name__)

PURGE_INTERVAL_SECONDS = 6 * 60 * 60


def purge_once() -> None:
    from src.api.dependencies import get_donation_store, get_snapshot_store

    donations = get_donation_store().purge(settings.DONATION_RETENTION_DAYS)
    snapshots = get_snapshot_store().purge(settings.SNAPSHOT_RETENTION_DAYS)
    if donations or snapshots:
        logger.info(
            "Retention: deleted %d donations and %d shared links",
            donations,
            snapshots,
        )


async def purge_forever() -> None:
    """Run at startup, then every few hours, until cancelled."""
    while True:
        try:
            await asyncio.to_thread(purge_once)
        except Exception:
            logger.exception("Retention purge failed")
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)
