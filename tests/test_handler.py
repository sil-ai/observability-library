"""Tests for the async LokiHandler.

Transport-only behaviour — payload-shape and send-failure guarantees are
asserted in test_payload.py and test_send_failure.py.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from observability_library import LokiHandler

from ._helpers import make_record


def test_extra_allowlist_validated_at_construction():
    with pytest.raises(ValueError, match="reserved body keys"):
        LokiHandler(url="http://loki/push", extra_allowlist={"level"})


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


def test_emit_logs_send_failure_when_no_event_loop():
    handler = LokiHandler(url="http://loki/push")
    with patch("observability_library.handler.log_send_failure") as failure:
        handler.emit(make_record())
    failure.assert_called_once()
    args, _ = failure.call_args
    assert args[0] == "async"


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
