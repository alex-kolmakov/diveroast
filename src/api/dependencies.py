import time
import uuid

from src.agent.conversation import DiverRoastAgent
from src.config import settings
from src.storage.donations import DonationStore
from src.storage.snapshots import LocalSnapshotStore, SnapshotStore

# In-memory session store: {session_id: DiverRoastAgent}, with last-access
# times for TTL eviction. The session ID is a private credential (chat,
# upload, dashboard); the public share link uses agent.share_id instead.
_sessions: dict[str, DiverRoastAgent] = {}
_last_seen: dict[str, float] = {}

# Singleton stores (lazily initialised)
_snapshot_store: SnapshotStore | None = None
_donation_store: DonationStore | None = None


def _evict(now: float) -> None:
    """Drop expired sessions, then the oldest ones if over MAX_SESSIONS."""
    expired = [
        sid
        for sid, seen in _last_seen.items()
        if now - seen > settings.SESSION_TTL_SECONDS
    ]
    for sid in expired:
        _sessions.pop(sid, None)
        _last_seen.pop(sid, None)
    while len(_sessions) >= settings.MAX_SESSIONS:
        oldest = min(_last_seen, key=_last_seen.__getitem__)
        _sessions.pop(oldest, None)
        _last_seen.pop(oldest, None)


def get_or_create_session(session_id: str | None = None) -> tuple[str, DiverRoastAgent]:
    """Get an existing session or create a new one.

    An unknown ``session_id`` is never adopted: the new session always gets
    a server-generated ID. Returns (session_id, agent) tuple.
    """
    existing = get_session(session_id) if session_id else None
    if existing is not None:
        return session_id, existing  # type: ignore[return-value]

    now = time.monotonic()
    _evict(now)
    new_id = str(uuid.uuid4())
    agent = DiverRoastAgent()
    _sessions[new_id] = agent
    _last_seen[new_id] = now
    return new_id, agent


def get_session(session_id: str) -> DiverRoastAgent | None:
    """Get an existing, unexpired session by ID, or None if not found."""
    now = time.monotonic()
    seen = _last_seen.get(session_id)
    if seen is None or now - seen > settings.SESSION_TTL_SECONDS:
        _sessions.pop(session_id, None)
        _last_seen.pop(session_id, None)
        return None
    _last_seen[session_id] = now
    return _sessions.get(session_id)


def get_snapshot_store() -> SnapshotStore:
    """Return the singleton snapshot store (local filesystem, backed by Docker volume in prod)."""
    global _snapshot_store
    if _snapshot_store is None:
        _snapshot_store = LocalSnapshotStore(
            settings.SNAPSHOT_DIR, settings.SNAPSHOTS_MAX_TOTAL_MB * 1024 * 1024
        )
    return _snapshot_store


def get_donation_store() -> DonationStore:
    global _donation_store
    if _donation_store is None:
        _donation_store = DonationStore(
            settings.DONATIONS_DIR, settings.DONATIONS_MAX_TOTAL_MB * 1024 * 1024
        )
    return _donation_store
