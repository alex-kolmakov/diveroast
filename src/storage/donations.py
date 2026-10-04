"""Donated dive logs: the sanitized file plus a consent record.

Layout in DONATIONS_DIR, per donation:
    {id}{ext}   the sanitized log (see src/storage/sanitize.py)
    {id}.json   who agreed to what and when, and how to delete it

The donor gets a deletion code ``{id}.{token}`` once; only a hash of the
token is kept.
"""

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Bump whenever the donation wording on the upload screen or the privacy
# page changes; the frontend sends the version it showed.
CONSENT_VERSION = "2026-10-04"

_ID = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True)
class Receipt:
    id: str
    deletion_code: str | None  # None for a duplicate: the first donor holds it
    status: str  # "stored" | "duplicate"


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


class DonationStore:
    def __init__(self, directory: str, max_total_bytes: int) -> None:
        self._dir = Path(directory)
        self._max_total_bytes = max_total_bytes
        self._lock = threading.Lock()

    def _records(self):
        for path in self._dir.glob("*.json"):
            try:
                yield json.loads(path.read_text())
            except (OSError, ValueError):
                logger.warning("Unreadable donation record %s", path.name)

    def _total_bytes(self) -> int:
        return sum(p.stat().st_size for p in self._dir.iterdir() if p.is_file())

    def save(
        self, clean: bytes, ext: str, *, dive_count: int, consent_version: str
    ) -> Receipt | None:
        """Store a sanitized log; None if the storage cap is reached."""
        digest = _sha256(clean)
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            for record in self._records():
                if record.get("sha256") == digest:
                    return Receipt(record["id"], None, "duplicate")
            if self._total_bytes() + len(clean) > self._max_total_bytes:
                logger.warning("Donation not stored: DONATIONS_MAX_TOTAL_MB reached")
                return None
            donation_id = secrets.token_hex(8)
            token = secrets.token_urlsafe(16)
            (self._dir / f"{donation_id}{ext}").write_bytes(clean)
            record = {
                "id": donation_id,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "consent_version": consent_version,
                "format": ext,
                "bytes": len(clean),
                "sha256": digest,
                "dive_count": dive_count,
                "token_sha256": _sha256(token),
            }
            self._write(record)
        logger.info("Donation %s stored (%d dives)", donation_id, dive_count)
        return Receipt(donation_id, f"{donation_id}.{token}", "stored")

    def _write(self, record: dict[str, Any]) -> None:
        path = self._dir / f"{record['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1))
        tmp.replace(path)

    def _load(self, donation_id: str) -> dict[str, Any] | None:
        if not _ID.match(donation_id):
            return None
        try:
            return json.loads((self._dir / f"{donation_id}.json").read_text())
        except (OSError, ValueError):
            return None

    def _remove(self, donation_id: str) -> None:
        for path in self._dir.glob(f"{donation_id}.*"):
            path.unlink(missing_ok=True)
        logger.info("Donation %s deleted", donation_id)

    def delete_with_code(self, code: str) -> bool:
        """Delete by the donor's code; False if it matches nothing."""
        donation_id, _, token = code.strip().partition(".")
        with self._lock:
            record = self._load(donation_id)
            if record is None or not hmac.compare_digest(
                _sha256(token), record.get("token_sha256", "")
            ):
                return False
            self._remove(donation_id)
        return True

    def delete(self, donation_id: str) -> bool:
        """Delete by id (admin)."""
        with self._lock:
            if self._load(donation_id) is None:
                return False
            self._remove(donation_id)
        return True
