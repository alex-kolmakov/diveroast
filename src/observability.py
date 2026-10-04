"""Observability: Phoenix tracing, Sentry error reports, operational alerts.

Call init_sentry() before the app is created and init_tracing() at startup.
"""

import logging
import threading
import time

from src.config import settings

logger = logging.getLogger(__name__)

_tracer = None


def _noop_tracer():
    """Return a no-op tracer when Phoenix is unavailable."""
    from opentelemetry.trace import NoOpTracer

    return NoOpTracer()


def init_tracing():
    global _tracer
    try:
        from phoenix.otel import register

        tracer_provider = register(
            project_name="diveroast",
            endpoint=settings.PHOENIX_COLLECTOR_ENDPOINT,
            auto_instrument=True,  # auto-instruments google-genai
        )
        _tracer = tracer_provider.get_tracer("diveroast")
    except Exception:
        logger.warning("Phoenix tracing unavailable, using no-op tracer", exc_info=True)
        _tracer = _noop_tracer()
    return _tracer


def get_tracer():
    global _tracer
    if _tracer is None:
        _tracer = init_tracing()
    return _tracer


def init_sentry(transport=None) -> bool:
    """Report errors to Sentry when SENTRY_DSN is set; a no-op otherwise.

    Privacy: no request bodies (logs, chat messages, deletion codes), no
    local variables in stack frames (they hold dive data and roasts), no
    IPs or headers. Errors only: no performance tracing.
    """
    if not settings.SENTRY_DSN:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.logging import ignore_logger

    # A Phoenix outage logs an exception per failed trace export; as Sentry
    # events those would spend the free quota in hours. It shows on /health
    # and in the container log instead.
    ignore_logger("opentelemetry.*")

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        traces_sample_rate=0.0,
        transport=transport,  # tests only
    )
    return True


_last_alert: dict[str, float] = {}
_alert_lock = threading.Lock()
ALERT_INTERVAL_SECONDS = 10 * 60


def alert(message: str, **context) -> None:
    """Log a warning and raise a Sentry issue for something to look at now.

    Throttled per message so an overload can't spend the Sentry quota: the
    same alert goes out at most once per ALERT_INTERVAL_SECONDS. Keep the
    message fixed and put the numbers in ``context`` so Sentry groups it.
    """
    logger.warning("%s %s", message, context or "")
    now = time.monotonic()
    with _alert_lock:
        if (
            now - _last_alert.get(message, -ALERT_INTERVAL_SECONDS)
            < ALERT_INTERVAL_SECONDS
        ):
            return
        _last_alert[message] = now
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            scope.set_context("alert", context)
            sentry_sdk.capture_message(message, level="warning")
    except Exception:
        logger.debug("Sentry unavailable for alert", exc_info=True)
