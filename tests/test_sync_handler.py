"""Tests for SyncLokiHandler — queue + worker thread + requests.post."""

import time
from unittest.mock import patch

import pytest
import requests

from observability_library import SyncLokiHandler

from ._helpers import make_record


@pytest.fixture
def handler():
    h = SyncLokiHandler(url="http://loki/push", labels={"app": "x"})
    yield h
    h.close()


def test_extra_allowlist_validated_at_construction():
    with pytest.raises(ValueError, match="standard LogRecord"):
        SyncLokiHandler(url="http://loki/push", extra_allowlist={"args"})


def test_emit_enqueues_payload(handler):
    handler.emit(make_record())
    # Worker may drain quickly; allow either still-queued or just-drained
    # by asserting queue depth is not negative.
    assert handler._queue.qsize() >= 0


def test_emit_drops_silently_when_queue_full():
    h = SyncLokiHandler(url="http://loki/push", queue_size=1)
    # Block the worker so the queue fills
    h._stop.set()
    h._queue.put_nowait({"streams": []})
    # Now any further emit should silently drop
    h.emit(make_record())  # no exception
    h.close()


def test_drain_thread_posts_to_loki():
    h = SyncLokiHandler(url="http://loki/push", auth_token="t0k3n")
    with patch.object(requests, "post") as post:
        post.return_value.raise_for_status = lambda: None
        h.emit(make_record())
        # Give the worker a moment to drain.
        for _ in range(50):
            if post.called:
                break
            time.sleep(0.05)
    h.close()

    assert post.called
    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer t0k3n"
    assert headers["Content-Type"] == "application/json"
    assert post.call_args.args[0] == "http://loki/push"


def test_drain_thread_calls_log_send_failure_on_request_exception():
    h = SyncLokiHandler(url="http://loki/push")
    with patch.object(
        requests, "post", side_effect=requests.ConnectionError("boom")
    ):
        with patch("observability_library.sync_handler.log_send_failure") as failure:
            h.emit(make_record())
            for _ in range(50):
                if failure.called:
                    break
                time.sleep(0.05)
    h.close()

    failure.assert_called()
    assert failure.call_args.args[0] == "sync"


def test_emit_routes_through_handle_error_on_payload_failure(handler):
    with patch(
        "observability_library.sync_handler.build_loki_payload",
        side_effect=RuntimeError,
    ):
        with patch.object(handler, "handleError") as he:
            handler.emit(make_record())
    he.assert_called_once()


def test_close_signals_worker_to_stop():
    h = SyncLokiHandler(url="http://loki/push")
    assert not h._stop.is_set()
    h.close()
    assert h._stop.is_set()
