"""Token accounting for model calls: logged, traced and counted for budgets."""

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from google.genai import types
from opentelemetry import trace

from src.config import settings
from src.observability import alert

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0  # includes thinking tokens
    cached: int = 0  # part of input served from Gemini's implicit cache

    @property
    def total(self) -> int:
        return self.input + self.output


def usage_of(response: types.GenerateContentResponse) -> Usage:
    meta = response.usage_metadata
    if meta is None:
        return Usage()
    return Usage(
        input=meta.prompt_token_count or 0,
        output=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
        cached=meta.cached_content_token_count or 0,
    )


class DailyBudget:
    """Tokens spent today (UTC) across every session: the circuit breaker.

    Sits below the billing alert. The count is saved to ``path`` after every
    model call, so a restart or a crash loop carries on from it instead of
    starting the day from zero; without a path it lives in memory only.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._day: date | None = None
        self._tokens = 0
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            saved = json.loads(self._path.read_text())
            self._day = date.fromisoformat(saved["day"])
            self._tokens = int(saved["tokens"])
        except (OSError, ValueError, KeyError, TypeError):
            alert("Daily token count unreadable; counting from zero")

    def _save(self) -> None:
        if self._path is None or self._day is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Written whole, then swapped in: a crash mid-write can't leave
            # half a file.
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"day": self._day.isoformat(), "tokens": self._tokens})
            )
            os.replace(tmp, self._path)
        except OSError as e:
            alert("Daily token count not saved; a restart would lose it", error=str(e))

    def _roll(self, today: date) -> None:
        if today != self._day:
            self._day, self._tokens = today, 0

    def add(self, tokens: int, today: date | None = None) -> None:
        with self._lock:
            self._roll(today or datetime.now(UTC).date())
            before = self._tokens
            self._tokens += tokens
            self._save()
            limit = settings.DAILY_MAX_TOKENS
            if before < limit <= self._tokens:
                alert(
                    "Daily token budget spent; roasts paused until midnight UTC",
                    tokens=self._tokens,
                    limit=limit,
                )

    def spent(self, today: date | None = None) -> bool:
        with self._lock:
            self._roll(today or datetime.now(UTC).date())
            return self._tokens >= settings.DAILY_MAX_TOKENS


daily_budget = DailyBudget(settings.DAILY_USAGE_FILE)


def record_usage(response: types.GenerateContentResponse, call: str) -> Usage:
    """Log and trace one model call's token counts, count them against the
    daily budget, and return them."""
    usage = usage_of(response)
    daily_budget.add(usage.total)
    logger.info(
        "model call %s: %d in (%d cached), %d out",
        call,
        usage.input,
        usage.cached,
        usage.output,
    )
    span = trace.get_current_span()
    span.set_attribute("diveroast.tokens.input", usage.input)
    span.set_attribute("diveroast.tokens.cached", usage.cached)
    span.set_attribute("diveroast.tokens.output", usage.output)
    return usage


def record_check(report) -> None:
    """Log and trace the answer guard's results (src.agent.checks.Report)."""
    if (
        report.stripped
        or report.banned
        or report.dan_without_link
        or report.format_issues
    ):
        logger.info(
            "answer check: %d sentences stripped (numbers %s, outcomes %s), "
            "banned %s, DAN unlinked %s, format %s",
            len(report.stripped),
            report.ungrounded,
            report.misattributed,
            report.banned,
            report.dan_without_link,
            report.format_issues,
        )
    span = trace.get_current_span()
    span.set_attribute("diveroast.check.stripped", len(report.stripped))
    span.set_attribute("diveroast.check.ungrounded", report.ungrounded)
    span.set_attribute("diveroast.check.misattributed", report.misattributed)
    span.set_attribute("diveroast.check.banned", report.banned)
    span.set_attribute("diveroast.check.dan_without_link", report.dan_without_link)
    span.set_attribute("diveroast.check.format", report.format_issues)
