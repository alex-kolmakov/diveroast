"""Per-client rate limits for the endpoints that cost money.

In-process, like the sessions: the backend runs as one uvicorn worker.
"""

import math
import threading
import time
from collections import deque

from fastapi import HTTPException, Request

from src.config import settings


class SlidingWindowLimiter:
    """Allow ``limit`` hits per key in any ``window`` seconds."""

    def __init__(self, limit: int, window: float, max_keys: int = 10_000):
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> float | None:
        """Record a hit; return seconds to wait if over the limit, else None."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self.max_keys:
                    self._prune(now)
                hits = self._hits[key] = deque()
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return self.window - (now - hits[0])
            hits.append(now)
            return None

    def _prune(self, now: float) -> None:
        stale = [
            k for k, h in self._hits.items() if not h or now - h[-1] >= self.window
        ]
        for k in stale:
            del self._hits[k]
        # Still full (a flood of distinct keys): drop the oldest half.
        if len(self._hits) >= self.max_keys:
            by_age = sorted(self._hits, key=lambda k: self._hits[k][-1])
            for k in by_age[: len(by_age) // 2]:
                del self._hits[k]


def client_ip(request: Request) -> str:
    """The caller's IP.

    Behind the production nginx (which restores the visitor's address from
    Cloudflare's header) X-Real-IP is trusted; anywhere else it could be
    forged, so the socket address is used.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-real-ip")
        if forwarded:
            return forwarded.strip()
    return request.client.host if request.client else "unknown"


upload_limiter = SlidingWindowLimiter(settings.UPLOADS_PER_IP_PER_HOUR, 3600)
chat_limiter = SlidingWindowLimiter(settings.CHATS_PER_IP_PER_HOUR, 3600)


def _enforce(limiter: SlidingWindowLimiter, request: Request, what: str) -> None:
    wait = limiter.hit(client_ip(request))
    if wait is not None:
        minutes = max(1, math.ceil(wait / 60))
        raise HTTPException(
            status_code=429,
            detail=f"Too many {what} from your network. Try again in {minutes} min.",
            headers={"Retry-After": str(math.ceil(wait))},
        )


def limit_uploads(request: Request) -> None:
    _enforce(upload_limiter, request, "uploads")


def limit_chats(request: Request) -> None:
    _enforce(chat_limiter, request, "messages")
