import json
import logging
import time
from typing import Dict, Iterable, Optional

import requests


_STANDARD_LOGRECORD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})


DEFAULT_EXTRA_ALLOWLIST = frozenset({
    "trace_id", "span_id", "trace_flags",
})


# Internal logger used for send-failure diagnostics. Set propagate=False so
# a misconfiguration that attaches a LokiHandler to the root logger cannot
# turn an emit failure into infinite recursion.
_internal_logger = logging.getLogger(__name__)
_internal_logger.propagate = False


class LokiHandler(logging.Handler):
    """Logging handler that ships records to Loki as structured JSON.

    Extra fields attached to a LogRecord (via `logger.info(..., extra={...})`
    or via a `logging.Filter`) are forwarded as JSON fields, but only if they
    appear in `extra_allowlist`. The default allowlist covers OTel correlation
    fields; extend it to permit additional safe application fields.

    Forwarding uses `getattr(record, key, None) is not None` — falsy values
    such as `0` and `""` are forwarded; only `None` (and missing attributes)
    are dropped.
    """

    def __init__(
        self,
        url: str,
        labels: Optional[Dict[str, str]] = None,
        timeout: int = 5,
        auth_token: Optional[str] = None,
        extra_allowlist: Optional[Iterable[str]] = None,
    ):
        super().__init__()
        self.url = url
        self.labels = labels or {}
        self.timeout = timeout
        self.auth_token = auth_token

        configured = frozenset(extra_allowlist or ())
        overlap = configured & _STANDARD_LOGRECORD_ATTRS
        if overlap:
            raise ValueError(
                "extra_allowlist cannot include standard LogRecord attributes "
                f"({sorted(overlap)}); these would forward unformatted "
                "internals (e.g. raw `args` may carry secrets)."
            )
        self.extra_allowlist = DEFAULT_EXTRA_ALLOWLIST | configured

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = self.build_payload(record)
            self.send_to_loki(payload)
        except Exception:
            self.handleError(record)

    def build_payload(self, record: logging.LogRecord) -> Dict:
        body = {
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        }
        for key in self.extra_allowlist:
            value = getattr(record, key, None)
            if value is not None:
                body[key] = value

        return {
            "streams": [
                {
                    "stream": self.labels,
                    "values": [[str(int(time.time() * 1_000_000_000)), json.dumps(body)]],
                }
            ]
        }

    def send_to_loki(self, payload: Dict) -> None:
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            response = requests.post(
                self.url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            # Log only the exception class — the message may contain the URL
            # with embedded credentials or other sensitive context.
            _internal_logger.error(
                "Failed to send log to Loki: %s", type(e).__name__
            )
