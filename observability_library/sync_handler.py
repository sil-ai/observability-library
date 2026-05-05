import json
import logging
import queue
import threading
import time
from typing import Dict, Optional

import requests


_STANDARD_RECORD_ATTRS = {
    "name", "msg", "args", "created", "relativeCreated", "exc_info",
    "exc_text", "stack_info", "lineno", "funcName", "pathname",
    "filename", "module", "thread", "threadName", "process",
    "processName", "levelname", "levelno", "message", "msecs",
    "taskName",
}


class SyncLokiHandler(logging.Handler):
    """Sync logging handler to send logs to Loki/Grafana.

    Designed for environments without a running asyncio event loop
    (Django/WSGI, Django sync views in the ASGI threadpool, scripts).

    ``emit`` is non-blocking: it serializes the record and pushes it onto
    a bounded queue. A single daemon thread drains the queue and POSTs
    to Loki synchronously via ``requests``. Records are dropped silently
    if the queue is full so that callers are never blocked.
    """

    def __init__(
        self,
        url: str,
        labels: Optional[Dict[str, str]] = None,
        timeout: int = 5,
        auth_token: Optional[str] = None,
        queue_size: int = 10000,
    ):
        super().__init__()
        self.url = url
        self.labels = labels or {}
        self.timeout = timeout
        self.auth_token = auth_token
        self._queue: "queue.Queue[Dict]" = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._worker = threading.Thread(
            target=self._drain, daemon=True, name="loki-handler"
        )
        self._worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = self._build_payload(record)
            self._queue.put_nowait(payload)
        except queue.Full:
            pass
        except Exception:
            self.handleError(record)

    def _build_payload(self, record: logging.LogRecord) -> Dict:
        log_entry = {
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_"):
                log_entry[key] = value

        timestamp = str(int(time.time() * 1_000_000_000))
        return {
            "streams": [
                {
                    "stream": self.labels,
                    "values": [[timestamp, json.dumps(log_entry, default=str)]],
                }
            ]
        }

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
            except requests.RequestException as e:
                logging.error(f"Failed to send log to Loki (sync): {e}")

    def close(self) -> None:
        self._stop.set()
        super().close()
