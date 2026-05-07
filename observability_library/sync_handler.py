import logging
import queue
import threading
from typing import Dict, Iterable, Optional

import requests

from ._payload import build_loki_payload, log_send_failure, validate_allowlist


class SyncLokiHandler(logging.Handler):
    """Sync logging handler to send logs to Loki/Grafana.

    Designed for environments without a running asyncio event loop
    (Django/WSGI, Django sync views in the ASGI threadpool, scripts).

    `emit` is non-blocking: it serialises the record and pushes it onto
    a bounded queue. A single daemon thread drains the queue and POSTs
    to Loki synchronously via `requests`. Records are dropped silently
    if the queue is full so callers are never blocked.

    Extra fields are filtered through `extra_allowlist` — see
    `LokiHandler` and `observability_library._payload` for the rationale.
    """

    def __init__(
        self,
        url: str,
        labels: Optional[Dict[str, str]] = None,
        timeout: int = 5,
        auth_token: Optional[str] = None,
        queue_size: int = 10000,
        extra_allowlist: Optional[Iterable[str]] = None,
    ):
        super().__init__()
        self.url = url
        self.labels = labels or {}
        self.timeout = timeout
        self.auth_token = auth_token
        self.extra_allowlist = validate_allowlist(extra_allowlist)
        self._queue: "queue.Queue[Dict]" = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._worker = threading.Thread(
            target=self._drain, daemon=True, name="loki-handler"
        )
        self._worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = build_loki_payload(
                record,
                self.format(record),
                self.labels,
                self.extra_allowlist,
            )
            self._queue.put_nowait(payload)
        except queue.Full:
            pass
        except Exception:
            self.handleError(record)

    def _drain(self) -> None:
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        while not self._stop.is_set():
            try:
                payload = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                response = requests.post(
                    self.url, json=payload, headers=headers, timeout=self.timeout
                )
                response.raise_for_status()
            except Exception as e:
                # Catching broadly is intentional: a ValueError from a
                # malformed URL or an OSError from a socket limit must not
                # kill the daemon thread, otherwise audit logs stop
                # shipping and the operator gets no signal.
                log_send_failure("sync", e)

    def close(self) -> None:
        self._stop.set()
        # Wait for the worker to drain in-flight items. Bounded by the
        # 1s queue timeout plus the request timeout, so close() returns
        # promptly even if Loki is unreachable.
        if self._worker.is_alive():
            self._worker.join(timeout=self.timeout + 2)
        super().close()
