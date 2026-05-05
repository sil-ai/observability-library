import logging
import json
import time
import asyncio
from typing import Dict, Optional
import aiohttp


_STANDARD_RECORD_ATTRS = {
    "name", "msg", "args", "created", "relativeCreated", "exc_info",
    "exc_text", "stack_info", "lineno", "funcName", "pathname",
    "filename", "module", "thread", "threadName", "process",
    "processName", "levelname", "levelno", "message", "msecs",
    "taskName",
}


class LokiHandler(logging.Handler):
    """
    Async logging handler to send logs to Loki/Grafana.

    This handler is designed exclusively for async environments (FastAPI, asyncio).
    It uses aiohttp for non-blocking HTTP requests.

    Requires an async event loop to be running.
    """

    def __init__(
        self,
        url: str,
        labels: Optional[Dict[str, str]] = None,
        timeout: int = 5,
        auth_token: Optional[str] = None,
    ):
        """
        Args:
            url: Loki endpoint URL (e.g., http://localhost:3100/loki/api/v1/push)
            labels: Dictionary of labels for Loki (e.g., {"app": "my-app", "env": "prod"})
            timeout: Timeout for HTTP requests in seconds
            auth_token: Bearer token for authentication (optional)
        """
        super().__init__()
        self.url = url
        self.labels = labels or {}
        self.timeout = timeout
        self.auth_token = auth_token
        self._session: Optional[aiohttp.ClientSession] = None

    def emit(self, record: logging.LogRecord) -> None:
        """
        Sends a log record to Loki asynchronously.

        Requires an async event loop to be running.
        Creates a task to send the log without blocking.
        """
        try:
            log_entry = self.format_record(record)
            loop = asyncio.get_running_loop()
            asyncio.create_task(self.async_send_to_loki(log_entry))
        except RuntimeError:
            logging.error("LokiHandler requires an async event loop. Use in FastAPI or asyncio applications.")
        except Exception:
            self.handleError(record)

    def format_record(self, record: logging.LogRecord) -> Dict:
        """
        Formats the log record for Loki, including any extra fields.
        """
        log_data = {
            "timestamp": str(int(time.time() * 1_000_000_000)),
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_"):
                log_data[key] = value

        return log_data

    async def async_send_to_loki(self, log_entry: Dict) -> None:
        """
        Sends the log to Loki via HTTP asynchronously.
        Uses aiohttp for non-blocking requests.
        """
        payload = self._build_payload(log_entry)
        headers = self._build_headers()

        try:
            # Create session if it doesn't exist
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=self.timeout)
                self._session = aiohttp.ClientSession(timeout=timeout)

            async with self._session.post(
                self.url,
                json=payload,
                headers=headers
            ) as response:
                response.raise_for_status()
        except aiohttp.ClientError as e:
            logging.error(f"Failed to send log to Loki (async): {e}")
        except Exception as e:
            logging.error(f"Unexpected error sending log to Loki: {e}")

    def _build_payload(self, log_entry: Dict) -> Dict:
        """Builds the Loki payload structure, including extra fields."""
        timestamp = log_entry.pop("timestamp")
        return {
            "streams": [
                {
                    "stream": self.labels,
                    "values": [
                        [
                            timestamp,
                            json.dumps(log_entry, default=str),
                        ]
                    ]
                }
            ]
        }

    def _build_headers(self) -> Dict[str, str]:
        """Builds HTTP headers for Loki requests."""
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def close(self) -> None:
        """
        Close the aiohttp session synchronously.

        Attempts to close the session if an event loop is running.
        Schedules the close operation without waiting for completion.

        This method is compatible with Python's logging.shutdown() which
        expects a synchronous close() method.
        """
        if self._session and not self._session.closed:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._session.close())
            except RuntimeError:
                # No event loop running, cannot close async session
                pass

    def __del__(self):
        """Cleanup resources on deletion."""
        if self._session and not self._session.closed:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._session.close())
            except RuntimeError:
                # No event loop, can't close async session
                pass