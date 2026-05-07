"""Shared helpers for both LokiHandler (async) and SyncLokiHandler.

The two handlers differ only in transport: payload construction, allowlist
validation, and send-failure diagnostics are identical and live here.

Security defaults established in this module:
  - Only fields in `extra_allowlist` are forwarded to Loki. Without this,
    `logger.info(..., extra={"Authorization": "Bearer xyz"})` would silently
    ship the secret as a JSON field on the record.
  - Fields colliding with standard `LogRecord` attributes (`args`, `msg`,
    etc.) are rejected at handler construction — they would forward
    unformatted internals.
  - Fields colliding with the body's reserved keys (`level`, `logger`,
    `message`) are rejected — they would let callers spoof severity etc.
  - Send failures log only the exception class via a dedicated module
    logger with `propagate=False`. The exception message can include the
    target URL with embedded credentials, and propagation to the root
    logger creates an infinite loop if a `LokiHandler` is attached there.
"""

import json
import logging
import time
from typing import Dict, Iterable, Mapping, Optional


_STANDARD_RECORD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})


_RESERVED_BODY_KEYS = frozenset({"level", "logger", "message"})


DEFAULT_EXTRA_ALLOWLIST: frozenset = frozenset({"trace_id", "span_id", "trace_flags"})


# Internal logger for send-failure diagnostics. propagate=False prevents
# the recursion-and-leak loop that would otherwise occur if a LokiHandler
# were attached to the root logger: an emit failure would log via root,
# which would re-emit through the same handler.
_send_failure_logger = logging.getLogger("observability_library._payload")
_send_failure_logger.propagate = False


def validate_allowlist(extra_allowlist: Optional[Iterable[str]]) -> frozenset:
    """Validate and normalise a user-supplied allowlist.

    Returns the union of `DEFAULT_EXTRA_ALLOWLIST` and the caller's
    additions. Raises `ValueError` if the caller's additions overlap
    standard `LogRecord` attribute names or the body's reserved keys.
    Raises `TypeError` if a bare string or bytes is passed — those are
    iterable-of-characters in Python, which would silently produce a
    per-character allowlist.
    """
    if isinstance(extra_allowlist, (str, bytes)):
        raise TypeError(
            "extra_allowlist must be an iterable of field names, not a "
            f"single {type(extra_allowlist).__name__}. Pass a set/list/"
            f"tuple, e.g. {{{extra_allowlist!r}}}."
        )
    configured = frozenset(extra_allowlist or ())

    standard_overlap = configured & _STANDARD_RECORD_ATTRS
    if standard_overlap:
        raise ValueError(
            "extra_allowlist cannot include standard LogRecord attributes "
            f"({sorted(standard_overlap)}); these would forward unformatted "
            "internals (e.g. raw `args` may carry secrets)."
        )

    reserved_overlap = configured & _RESERVED_BODY_KEYS
    if reserved_overlap:
        raise ValueError(
            "extra_allowlist cannot include reserved body keys "
            f"({sorted(reserved_overlap)}); these would let callers spoof "
            "level/logger/message via `extra={...}`."
        )

    return DEFAULT_EXTRA_ALLOWLIST | configured


def build_loki_payload(
    record: logging.LogRecord,
    formatted_message: str,
    labels: Mapping[str, str],
    allowlist: frozenset,
) -> Dict:
    """Build a Loki streams payload for a single log record.

    `formatted_message` should be the result of `handler.format(record)` —
    we accept it as a parameter so the handler subclass owns formatter
    selection (and so this helper has no `Handler` dependency, which
    keeps it trivially testable).

    Forwarding semantics: a field is included when it appears in
    `allowlist` and `getattr(record, field, None) is not None`. Falsy
    values (`0`, `""`, `False`) are forwarded — only `None` and missing
    attributes are dropped.
    """
    body = {
        "level": record.levelname,
        "logger": record.name,
        "message": formatted_message,
    }
    for key in allowlist:
        value = getattr(record, key, None)
        if value is not None:
            body[key] = value

    timestamp = str(time.time_ns())
    return {
        "streams": [
            {
                "stream": dict(labels),
                "values": [[timestamp, _safe_json_dumps(body)]],
            }
        ]
    }


def _safe_json_dumps(body: Dict) -> str:
    """Serialize the body, tolerating extras whose __str__ raises.

    `json.dumps(..., default=str)` calls `str(value)` on any unknown type.
    A buggy `__str__` (recursion, raises) would otherwise propagate out of
    `emit` via `handleError`, dropping the record entirely. We fall back
    to `repr(value)`; if even that fails we drop the offending key with a
    placeholder so the rest of the body still ships.
    """
    try:
        return json.dumps(body, default=str)
    except Exception:
        sanitised = {}
        for key, value in body.items():
            try:
                json.dumps(value, default=str)
                sanitised[key] = value
            except Exception:
                try:
                    sanitised[key] = repr(value)
                except Exception:
                    sanitised[key] = "<unserialisable>"
        return json.dumps(sanitised, default=str)


def log_send_failure(handler_kind: str, exc: BaseException) -> None:
    """Log a Loki send failure without leaking the exception's str().

    `requests` and `aiohttp` exception messages can contain the target
    URL — which may include embedded credentials — so we log only the
    class name plus a short label identifying the handler kind (`async`
    or `sync`). Use a dedicated non-propagating logger so a
    misconfiguration that attaches a LokiHandler to the root logger
    cannot turn an emit failure into an infinite loop.
    """
    _send_failure_logger.error(
        "Failed to send log to Loki (%s): %s", handler_kind, type(exc).__name__
    )
