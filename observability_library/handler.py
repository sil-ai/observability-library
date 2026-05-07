import asyncio
import logging
from typing import Dict, Iterable, Optional

import aiohttp

from ._payload import build_loki_payload, log_send_failure, validate_allowlist


class LokiHandler(logging.Handler):
    """Async logging handler to send logs to Loki/Grafana.

    Designed for async environments (FastAPI, asyncio). Uses aiohttp for
    non-blocking HTTP requests and requires a running event loop.

    Extra fields attached to a LogRecord (via `logger.info(..., extra={...})`
    or via a `logging.Filter` such as `TraceContextFilter`) are forwarded
    as JSON fields on the Loki payload, but only if they appear in
    `extra_allowlist`. The default allowlist covers OTel correlation
    fields; extend it explicitly for any application fields you want
    shipped. See `observability_library._payload` for security rationale.
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
        self.extra_allowlist = validate_allowlist(extra_allowlist)
        self._session: Optional[aiohttp.ClientSession] = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = build_loki_payload(
                record,
                self.format(record),
                self.labels,
                self.extra_allowlist,
            )
        except Exception:
            self.handleError(record)
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log_send_failure("async", RuntimeError("no running event loop"))
            return

        loop.create_task(self._async_send(payload))

    async def _async_send(self, payload: Dict) -> None:
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                )

            async with self._session.post(
                self.url,
                json=payload,
                headers=self._build_headers(),
            ) as response:
                response.raise_for_status()
        except Exception as e:
            log_send_failure("async", e)

    def _build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def close(self) -> None:
        """Schedule the aiohttp session for closure on the running loop.

        Compatible with `logging.shutdown()`, which expects a synchronous
        `close()`. If a loop is running we fire-and-forget; otherwise we
        run the coroutine on a fresh loop so the connector closes
        cleanly. For long-lived async applications, prefer `aclose()` —
        it gives you a coroutine you can `await` during shutdown.
        """
        session = getattr(self, "_session", None)
        if session and not session.closed:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(session.close())
            except RuntimeError:
                # No loop running — drive the close on a temporary one
                # so we don't leak the connector. Skip on any failure.
                try:
                    asyncio.run(session.close())
                except Exception:
                    pass
            except Exception:
                pass
        super().close()

    async def aclose(self) -> None:
        """Async-friendly close. Await this during shutdown to guarantee
        the aiohttp session and its connector are closed cleanly.

        ```python
        handler = LokiHandler(...)
        try:
            ...
        finally:
            await handler.aclose()
        ```
        """
        session = getattr(self, "_session", None)
        if session and not session.closed:
            await session.close()
        super().close()

    def __del__(self):
        # Defensive: __del__ can fire on a partially-constructed instance
        # if __init__ raised (e.g. validate_allowlist rejected the args).
        session = getattr(self, "_session", None)
        if session and not session.closed:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(session.close())
            except Exception:
                pass
