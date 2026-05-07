"""Tests for SyncLokiHandler — queue + worker thread + requests.post."""

import threading
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


def test_emit_enqueues_payload_with_canonical_body():
    # Block the worker so the queue retains items, then assert what we
    # actually put on it.
    h = SyncLokiHandler(url="http://loki/push", labels={"app": "x"})
    h._stop.set()
    h.emit(make_record())

    assert h._queue.qsize() == 1
    payload = h._queue.get_nowait()
    assert payload["streams"][0]["stream"] == {"app": "x"}
    h.close()


def test_emit_drops_silently_when_queue_full():
    h = SyncLokiHandler(url="http://loki/push", queue_size=1)
    h._stop.set()
    h._queue.put_nowait({"streams": []})
    h.emit(make_record())  # no exception
    assert h._queue.qsize() == 1  # original payload still there, second dropped
    h.close()


def test_drain_thread_posts_to_loki():
    h = SyncLokiHandler(url="http://loki/push", auth_token="t0k3n")
    posted = threading.Event()

    def fake_post(*a, **kw):
        posted.set()
        response = requests.Response()
        response.status_code = 204
        return response

    with patch.object(requests, "post", side_effect=fake_post) as post:
        h.emit(make_record())
        assert posted.wait(timeout=2), "drain thread did not POST in time"

    h.close()
    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer t0k3n"
    assert headers["Content-Type"] == "application/json"
    assert post.call_args.args[0] == "http://loki/push"


def test_drain_thread_calls_log_send_failure_on_request_exception():
    h = SyncLokiHandler(url="http://loki/push")
    fired = threading.Event()

    def trip(*a, **kw):
        fired.set()
        raise requests.ConnectionError("boom")

    with patch.object(requests, "post", side_effect=trip):
        with patch("observability_library.sync_handler.log_send_failure") as failure:
            h.emit(make_record())
            assert fired.wait(timeout=2)
            # Failure logger is called inside the drain loop, after the
            # mocked post raises. The post-side fired event guarantees we
            # waited until at least one drain iteration completed.
            assert failure.called
            assert failure.call_args.args[0] == "sync"
    h.close()


def test_drain_thread_survives_unexpected_exceptions():
    # A non-RequestException (e.g. ValueError from a malformed URL) used
    # to kill the daemon thread silently. It should now be caught and
    # logged like any other send failure.
    h = SyncLokiHandler(url="http://loki/push")
    fired = threading.Event()

    def trip(*a, **kw):
        fired.set()
        raise ValueError("bad url")

    with patch.object(requests, "post", side_effect=trip):
        with patch("observability_library.sync_handler.log_send_failure") as failure:
            h.emit(make_record())
            assert fired.wait(timeout=2)
            assert failure.called
            assert failure.call_args.args[0] == "sync"

    # Drain thread should still be alive after the bad call.
    assert h._worker.is_alive()
    h.close()


def test_emit_routes_through_handle_error_on_payload_failure(handler):
    with patch(
        "observability_library.sync_handler.build_loki_payload",
        side_effect=RuntimeError,
    ):
        with patch.object(handler, "handleError") as he:
            handler.emit(make_record())
    he.assert_called_once()


def test_close_signals_worker_and_joins():
    h = SyncLokiHandler(url="http://loki/push")
    assert h._worker.is_alive()
    h.close()
    assert h._stop.is_set()
    # close() now joins the worker, so the thread must have exited by
    # the time close() returns.
    assert not h._worker.is_alive()
