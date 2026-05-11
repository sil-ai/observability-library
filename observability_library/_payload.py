"""Shared helpers for both LokiHandler (async) and SyncLokiHandler.

The two handlers differ only in transport. Payload construction and
send-failure diagnostics are identical and live here.

Send-failure logging routes through a dedicated module logger with
`propagate=False`. The exception message from `requests`/`aiohttp` can
include the target URL with embedded credentials, so we log only the
exception class — and propagation to the root logger is suppressed so a
misconfiguration that attaches a Loki handler at the root cannot turn an
emit failure into an infinite loop.
"""

import json
import logging
import time
from typing import Dict, Mapping


_STANDARD_RECORD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})


_send_failure_logger = logging.getLogger("observability_library._payload")
_send_failure_logger.propagate = False


def build_loki_payload(
    record: logging.LogRecord,
    formatted_message: str,
    labels: Mapping[str, str],
) -> Dict:
    """Build a Loki streams payload for a single log record.

    Forwards every non-standard attribute on `record` as a JSON field,
    same as the previous wholesale behaviour. Standard `LogRecord`
    attributes and any underscore-prefixed attributes are filtered out.

    `formatted_message` should be `handler.format(record)` — accepted
    as a parameter so the handler owns formatter selection and this
    helper has no `Handler` dependency.
    """
    body = {
        "level": record.levelname,
        "logger": record.name,
        "message": formatted_message,
    }
    for key, value in record.__dict__.items():
        if key in _STANDARD_RECORD_ATTRS or key.startswith("_"):
            continue
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
    """Log a Loki send failure without leaking the exception's str()."""
    _send_failure_logger.error(
        "Failed to send log to Loki (%s): %s", handler_kind, type(exc).__name__
    )
