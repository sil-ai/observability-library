"""Tests for the async LokiHandler.

Transport-only behaviour — payload-shape and send-failure guarantees are
asserted in test_payload.py and test_send_failure.py.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from observability_library import LokiHandler
from observability_library.handler import (
    NoRunningEventLoopError,
    ThreadFallbackQueueFullError,
)

from ._helpers import make_record


def test_emit_creates_task_when_loop_running():
    handler = LokiHandler(url="http://loki/push", labels={"app": "x"})
    record = make_record()

    scheduled = []

    def capture(coro):
        scheduled.append(coro)

    fake_loop = MagicMock()
    fake_loop.create_task.side_effect = capture
    with patch("asyncio.get_running_loop", return_value=fake_loop):
        handler.emit(record)
    fake_loop.create_task.assert_called_once()
    # Close the captured coroutine so pytest doesn't warn about
    # never-awaited coroutines in this synchronous test.
    for coro in scheduled:
        coro.close()


def test_emit_routes_to_thread_fallback_when_no_event_loop():
    handler = LokiHandler(url="http://loki/push")
    try:
        fake_fallback = MagicMock()
        with patch.object(handler, "_get_thread_fallback", return_value=fake_fallback):
            handler.emit(make_record())
        fake_fallback.enqueue_payload.assert_called_once()
        payload = fake_fallback.enqueue_payload.call_args.args[0]
        assert "streams" in payload
    finally:
        handler.close()


def test_emit_falls_back_when_loop_create_task_raises_runtime_error():
    """Race seen in practice: get_running_loop() succeeds but the loop
    closes before create_task is called (typical near worker/interpreter
    shutdown). The handler should fall through to the sync fallback
    rather than letting the RuntimeError escape."""
    handler = LokiHandler(url="http://loki/push")
    try:
        fake_loop = MagicMock()
        fake_loop.create_task.side_effect = RuntimeError("Event loop is closed")
        fake_fallback = MagicMock()
        fake_fallback.enqueue_payload.return_value = True
        with patch("asyncio.get_running_loop", return_value=fake_loop), \
             patch.object(handler, "_get_thread_fallback", return_value=fake_fallback):
            handler.emit(make_record())
        fake_fallback.enqueue_payload.assert_called_once()
    finally:
        handler.close()


def test_emit_logs_send_failure_when_fallback_disabled_and_no_loop():
    handler = LokiHandler(url="http://loki/push", enable_thread_fallback=False)
    with patch("observability_library.handler.log_send_failure") as failure:
        handler.emit(make_record())
    failure.assert_called_once()
    args, _ = failure.call_args
    assert args[0] == "async"
    assert isinstance(args[1], NoRunningEventLoopError)


def test_fallback_construction_failure_is_reported_then_path_disabled():
    handler = LokiHandler(url="http://loki/push")
    with patch(
        "observability_library.handler.SyncLokiHandler",
        side_effect=RuntimeError("cannot build"),
    ):
        with patch("observability_library.handler.log_send_failure") as failure:
            assert handler._get_thread_fallback() is None
            # Second call must not retry construction nor re-log.
            assert handler._get_thread_fallback() is None
    failure.assert_called_once()
    args, _ = failure.call_args
    assert args[0] == "async"
    assert isinstance(args[1], RuntimeError)
    assert handler._enable_thread_fallback is False


def test_thread_fallback_is_lazy():
    handler = LokiHandler(url="http://loki/push")
    assert handler._fallback is None  # not built until needed
    handler.close()


def test_thread_fallback_built_once_and_reused():
    handler = LokiHandler(url="http://loki/push")
    try:
        first = handler._get_thread_fallback()
        second = handler._get_thread_fallback()
        assert first is not None
        assert first is second
    finally:
        handler.close()


def test_close_tears_down_fallback():
    handler = LokiHandler(url="http://loki/push")
    fallback = handler._get_thread_fallback()
    assert fallback is not None
    handler.close()
    assert fallback._stop.is_set()
    assert not fallback._worker.is_alive()
    # Fallback is cleared so a subsequent emit() doesn't enqueue into a
    # dead worker.
    assert handler._fallback is None


async def test_close_offloads_fallback_teardown_when_loop_running():
    handler = LokiHandler(url="http://loki/push")
    fake_fallback = MagicMock()
    handler._fallback = fake_fallback
    fake_loop = MagicMock()
    with patch("asyncio.get_running_loop", return_value=fake_loop):
        handler.close()
    # Inside a running loop, close() routes the join through
    # run_in_executor instead of calling fallback.close() directly.
    fake_loop.run_in_executor.assert_called_once_with(None, fake_fallback.close)
    fake_fallback.close.assert_not_called()
    assert handler._fallback is None
    assert handler._enable_thread_fallback is False


def test_del_does_not_block_on_fallback_join():
    handler = LokiHandler(url="http://loki/push")
    fake_fallback = MagicMock()
    fake_fallback._stop = MagicMock()
    handler._fallback = fake_fallback
    handler.__del__()
    # __del__ must signal stop but must NOT call close() (which joins).
    fake_fallback._stop.set.assert_called_once()
    fake_fallback.close.assert_not_called()


async def test_aclose_tears_down_fallback():
    handler = LokiHandler(url="http://loki/push")
    fallback = handler._get_thread_fallback()
    assert fallback is not None
    await handler.aclose()
    assert fallback._stop.is_set()
    assert not fallback._worker.is_alive()
    assert handler._fallback is None


def test_emit_logs_send_failure_when_fallback_queue_full():
    handler = LokiHandler(url="http://loki/push")
    try:
        full_fallback = MagicMock()
        full_fallback.enqueue_payload.return_value = False
        with patch.object(handler, "_get_thread_fallback", return_value=full_fallback):
            with patch("observability_library.handler.log_send_failure") as failure:
                handler.emit(make_record())
        failure.assert_called_once()
        assert failure.call_args.args[0] == "async"
        assert isinstance(failure.call_args.args[1], ThreadFallbackQueueFullError)
    finally:
        handler.close()


def test_emit_after_close_does_not_re_enqueue_to_dead_fallback():
    handler = LokiHandler(url="http://loki/push")
    handler._get_thread_fallback()  # build it
    handler.close()
    with patch("observability_library.handler.log_send_failure") as failure:
        handler.emit(make_record())
    failure.assert_called_once()
    assert failure.call_args.args[0] == "async"


def test_emit_routes_through_handle_error_on_payload_failure():
    handler = LokiHandler(url="http://loki/push")
    with patch(
        "observability_library.handler.build_loki_payload",
        side_effect=RuntimeError,
    ):
        with patch.object(handler, "handleError") as he:
            handler.emit(make_record())
    he.assert_called_once()


async def test_async_send_posts_payload_with_auth_header():
    handler = LokiHandler(
        url="http://loki/push",
        labels={"app": "x"},
        auth_token="t0k3n",
    )
    payload = {"streams": []}

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()

    async def post_ctx(*a, **kw):
        return fake_response

    fake_session = MagicMock()
    fake_session.closed = False
    # Bind to the current loop so the handler's loop-mismatch check
    # treats this cached session as fresh.
    import asyncio as _asyncio
    fake_session._loop = _asyncio.get_running_loop()
    fake_session.post = MagicMock()
    fake_session.post.return_value.__aenter__ = AsyncMock(return_value=fake_response)
    fake_session.post.return_value.__aexit__ = AsyncMock(return_value=None)

    handler._session = fake_session

    await handler._async_send(payload)

    fake_session.post.assert_called_once()
    kwargs = fake_session.post.call_args.kwargs
    args = fake_session.post.call_args.args
    assert args[0] == "http://loki/push"
    assert kwargs["json"] == payload
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["headers"]["Authorization"] == "Bearer t0k3n"

    # Detach the mock session so __del__ doesn't try to close it
    # (closed=False on a MagicMock would otherwise trip the cleanup path).
    handler._session = None


async def test_async_send_logs_send_failure_on_exception():
    handler = LokiHandler(url="http://loki/push")

    fake_session = MagicMock()
    fake_session.closed = False
    fake_session.post = MagicMock(side_effect=RuntimeError("boom"))
    handler._session = fake_session

    with patch("observability_library.handler.log_send_failure") as failure:
        await handler._async_send({"streams": []})

    failure.assert_called_once()
    assert failure.call_args.args[0] == "async"
    handler._session = None


def test_close_is_safe_without_session():
    handler = LokiHandler(url="http://loki/push")
    handler.close()  # no exception


async def test_aclose_awaits_session_close():
    handler = LokiHandler(url="http://loki/push")
    closed = []

    async def fake_close():
        closed.append(True)

    fake_session = MagicMock()
    fake_session.closed = False
    fake_session.close = fake_close
    handler._session = fake_session

    await handler.aclose()
    assert closed == [True]
    handler._session = None


async def test_close_schedules_session_close_when_loop_running():
    handler = LokiHandler(url="http://loki/push")

    closed = []

    async def fake_close():
        closed.append(True)

    fake_session = MagicMock()
    fake_session.closed = False
    fake_session.close = fake_close
    handler._session = fake_session

    handler.close()
    # Yield to the loop so the scheduled close coroutine runs.
    import asyncio as _asyncio
    await _asyncio.sleep(0)
    assert closed == [True]
    handler._session = None


async def test_async_send_recreates_session_when_loop_changed():
    """Modal workers can run a function's logging from a different event
    loop than the one a cached aiohttp ClientSession was bound to. The
    handler must detect that and rebuild the session, otherwise aiohttp
    raises 'Timeout context manager should be used inside a task'."""
    handler = LokiHandler(url="http://loki/push")

    stale = MagicMock()
    stale.closed = False
    stale._loop = MagicMock()  # different loop object than the running one
    handler._session = stale

    with patch("aiohttp.ClientSession") as ClientSession:
        new_session = MagicMock()
        new_session.closed = False
        new_session.post.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(raise_for_status=MagicMock())
        )
        new_session.post.return_value.__aexit__ = AsyncMock(return_value=None)
        ClientSession.return_value = new_session

        await handler._async_send({"streams": []})

    assert handler._session is new_session  # stale session was replaced
    handler._session = None


async def test_async_send_creates_session_when_existing_one_is_closed():
    handler = LokiHandler(url="http://loki/push")

    stale = MagicMock()
    stale.closed = True
    handler._session = stale

    with patch("aiohttp.ClientSession") as ClientSession:
        new_session = MagicMock()
        new_session.closed = False
        new_session.post.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(raise_for_status=MagicMock())
        )
        new_session.post.return_value.__aexit__ = AsyncMock(return_value=None)
        ClientSession.return_value = new_session

        await handler._async_send({"streams": []})

    assert handler._session is new_session
    handler._session = None


def test_del_on_partially_constructed_instance_does_not_raise():
    handler = LokiHandler.__new__(LokiHandler)
    # __init__ never ran, so _session is missing — __del__ must tolerate.
    handler.__del__()


def test_handler_inherits_logging_handler():
    assert isinstance(LokiHandler(url="http://loki/push"), logging.Handler)
