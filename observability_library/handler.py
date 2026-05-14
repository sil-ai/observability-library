import asyncio
import logging
import threading
from typing import Dict, Optional

import aiohttp

from ._payload import build_loki_payload, log_send_failure
from .sync_handler import SyncLokiHandler


class NoRunningEventLoopError(RuntimeError):
    """Raised internally when `emit` runs on a thread with no loop and
    no fallback is available. Surfaced via `log_send_failure` so the
    class name distinguishes it from other RuntimeError causes."""


class ThreadFallbackQueueFullError(RuntimeError):
    """Raised internally when the thread-fallback queue is saturated.
    Distinct from `NoRunningEventLoopError` so operators can tell the
    two drop-paths apart from the logged class name alone."""


class LokiHandler(logging.Handler):
    """Async logging handler to send logs to Loki/Grafana.

    Designed for async environments (FastAPI, asyncio). Uses aiohttp for
    non-blocking HTTP requests on the running event loop.

    When `emit` is called from a thread with no running event loop
    (e.g. work dispatched via `asyncio.to_thread`, or a thread pool
    inside an otherwise-async app), the record is routed to a lazily
    initialised `SyncLokiHandler` so it still ships to Loki via a
    background worker thread. Set `enable_thread_fallback=False` to
    restore the strict async-only behaviour.

    Every non-standard attribute on the `LogRecord` is forwarded as a
    JSON field — pass application fields via `logger.info(..., extra={...})`
    or stamp them on with a `logging.Filter` (e.g. `TraceContextFilter`).
    """

    def __init__(
        self,
        url: str,
        labels: Optional[Dict[str, str]] = None,
        timeout: int = 5,
        auth_token: Optional[str] = None,
        enable_thread_fallback: bool = True,
    ):
        super().__init__()
        self.url = url
        self.labels = labels or {}
        self.timeout = timeout
        self.auth_token = auth_token
        self._session: Optional[aiohttp.ClientSession] = None
        self._enable_thread_fallback = enable_thread_fallback
        self._fallback: Optional[SyncLokiHandler] = None
        self._fallback_lock = threading.Lock()

    def _get_thread_fallback(self) -> Optional[SyncLokiHandler]:
        """Lazily create the sync handler used when no loop is running.

        The fallback is only spawned on first need so async-only callers
        don't pay for an idle thread + queue.
        """
        if not self._enable_thread_fallback:
            return None
        if self._fallback is not None:
            return self._fallback
        with self._fallback_lock:
            if self._fallback is None:
                try:
                    self._fallback = SyncLokiHandler(
                        url=self.url,
                        labels=self.labels,
                        timeout=self.timeout,
                        auth_token=self.auth_token,
                    )
                except Exception as e:
                    # If we can't build the fallback, disable the path
                    # so we don't retry on every emit, and surface the
                    # cause once so operators don't see only the
                    # downstream "no running event loop" symptom.
                    self._enable_thread_fallback = False
                    log_send_failure("async", e)
        return self._fallback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = build_loki_payload(record, self.format(record), self.labels)
        except Exception:
            self.handleError(record)
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            try:
                loop.create_task(self._async_send(payload))
                return
            except RuntimeError:
                # `get_running_loop` succeeded but the loop has since
                # closed (typical near interpreter / worker shutdown).
                # Fall through to the thread fallback so the record
                # still ships.
                pass

        fallback = self._get_thread_fallback()
        if fallback is not None:
            if not fallback.enqueue_payload(payload):
                log_send_failure("async", ThreadFallbackQueueFullError())
            return

        log_send_failure("async", NoRunningEventLoopError())

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
        fallback_lock = getattr(self, "_fallback_lock", None)
        if fallback_lock is not None:
            with fallback_lock:
                # Flip the gate before joining so a concurrent emit() in
                # _get_thread_fallback() can't spin up a new worker after
                # we've torn the current one down.
                self._enable_thread_fallback = False
                fallback = self._fallback
                self._fallback = None
            if fallback is not None:
                try:
                    loop = None
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        pass
                    if loop is not None:
                        # Joining the worker would block the running
                        # loop for up to `timeout + 2`s — fire-and-forget
                        # via the default executor instead, matching the
                        # session-close pattern above.
                        loop.run_in_executor(None, fallback.close)
                    else:
                        fallback.close()
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
        fallback_lock = getattr(self, "_fallback_lock", None)
        if fallback_lock is not None:
            with fallback_lock:
                self._enable_thread_fallback = False
                fallback = self._fallback
                self._fallback = None
            if fallback is not None:
                try:
                    # `SyncLokiHandler.close()` joins the worker thread
                    # for up to `timeout + 2`s; offload so we don't
                    # block the loop.
                    await asyncio.get_running_loop().run_in_executor(
                        None, fallback.close
                    )
                except Exception:
                    pass
        super().close()

    def __del__(self):
        # Defensive: __del__ can fire on a partially-constructed instance
        # if __init__ raised.
        session = getattr(self, "_session", None)
        if session and not session.closed:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(session.close())
            except Exception:
                pass
        fallback = getattr(self, "_fallback", None)
        if fallback is not None:
            # Don't call fallback.close() here — it joins the worker
            # thread for up to `timeout + 2`s, which can stall
            # interpreter shutdown. Just signal stop and let the
            # daemon thread exit on its own. Callers that need an
            # ordered drain should use close()/aclose() explicitly.
            try:
                fallback._stop.set()
            except Exception:
                pass
