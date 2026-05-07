import json
import logging
from unittest.mock import patch

import pytest
import requests

from observability_library import LokiHandler
from observability_library.handler import _internal_logger


def _make_record(**extra):
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_payload_contains_message_and_level():
    handler = LokiHandler(url="http://loki/push", labels={"app": "x"})
    record = _make_record()
    payload = handler.build_payload(record)
    body = json.loads(payload["streams"][0]["values"][0][1])
    assert body["level"] == "INFO"
    assert body["message"] == "hello world"
    assert body["logger"] == "test"


def test_payload_propagates_labels_verbatim():
    labels = {"app": "x", "env": "prod", "service": "api"}
    handler = LokiHandler(url="http://loki/push", labels=labels)
    payload = handler.build_payload(_make_record())
    assert payload["streams"][0]["stream"] == labels


def test_payload_uses_nanosecond_epoch_timestamp():
    handler = LokiHandler(url="http://loki/push")
    payload = handler.build_payload(_make_record())
    timestamp = payload["streams"][0]["values"][0][0]
    assert timestamp.isdigit()
    # Nanosecond epoch since 2001 — anything below this is the wrong scale.
    assert int(timestamp) > 1_000_000_000_000_000_000


def test_default_allowlist_includes_trace_context():
    handler = LokiHandler(url="http://loki/push")
    record = _make_record(trace_id="a" * 32, span_id="b" * 16)
    body = json.loads(handler.build_payload(record)["streams"][0]["values"][0][1])
    assert body["trace_id"] == "a" * 32
    assert body["span_id"] == "b" * 16


def test_extra_allowlist_filters_unallowed_fields():
    handler = LokiHandler(url="http://loki/push", extra_allowlist={"assessment_id"})
    record = _make_record(assessment_id=42, secret_token="leak")
    body = json.loads(handler.build_payload(record)["streams"][0]["values"][0][1])
    assert body["assessment_id"] == 42
    assert "secret_token" not in body


def test_extra_allowlist_allows_falsy_values_but_drops_none():
    handler = LokiHandler(url="http://loki/push", extra_allowlist={"count", "missing"})
    record = _make_record(count=0)
    body = json.loads(handler.build_payload(record)["streams"][0]["values"][0][1])
    assert body["count"] == 0
    assert "missing" not in body


def test_extra_allowlist_rejects_overlap_with_standard_attrs():
    with pytest.raises(ValueError, match="standard LogRecord"):
        LokiHandler(url="http://loki/push", extra_allowlist={"args", "msg"})


def test_send_to_loki_posts_correct_url_and_content_type():
    handler = LokiHandler(url="http://loki/push", labels={"app": "x"})
    payload = handler.build_payload(_make_record())
    with patch.object(requests, "post") as post:
        post.return_value.raise_for_status = lambda: None
        handler.send_to_loki(payload)

    post.assert_called_once()
    assert post.call_args.args[0] == "http://loki/push"
    assert post.call_args.kwargs["json"] == payload
    assert post.call_args.kwargs["headers"]["Content-Type"] == "application/json"
    assert post.call_args.kwargs["timeout"] == 5


def test_send_to_loki_handles_http_error_response():
    handler = LokiHandler(url="http://loki/push")

    def raise_for_status():
        raise requests.HTTPError("500 Server Error")

    with patch.object(requests, "post") as post:
        post.return_value.raise_for_status = raise_for_status
        with patch.object(_internal_logger, "error") as err:
            handler.send_to_loki({"streams": []})
    err.assert_called_once()
    args, kwargs = err.call_args
    assert "HTTPError" in str(args[1])


def test_send_failure_logs_only_exception_class():
    handler = LokiHandler(url="http://loki:secret@host/push")

    def boom(*a, **kw):
        raise requests.ConnectionError("http://user:pw@host/leaked")

    with patch.object(requests, "post", side_effect=boom):
        with patch.object(_internal_logger, "error") as err:
            handler.send_to_loki({"streams": []})

    err.assert_called_once()
    args, kwargs = err.call_args
    assert args[1] == "ConnectionError"
    full_message = args[0] % args[1:]
    assert "user:pw" not in full_message
    assert "leaked" not in full_message


def test_internal_logger_does_not_propagate():
    # Recursion guard: if the root logger has a LokiHandler attached, an
    # emit failure would re-enter emit() via propagation and stack-overflow.
    assert _internal_logger.propagate is False


def test_auth_token_attached_as_bearer():
    handler = LokiHandler(url="http://loki/push", auth_token="t0k3n")
    with patch.object(requests, "post") as post:
        post.return_value.raise_for_status = lambda: None
        handler.send_to_loki({"streams": []})
    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer t0k3n"


def test_no_auth_header_when_token_absent():
    handler = LokiHandler(url="http://loki/push")
    with patch.object(requests, "post") as post:
        post.return_value.raise_for_status = lambda: None
        handler.send_to_loki({"streams": []})
    assert "Authorization" not in post.call_args.kwargs["headers"]


def test_emit_routes_through_handle_error_on_format_failure():
    handler = LokiHandler(url="http://loki/push")
    record = _make_record()

    with patch.object(handler, "build_payload", side_effect=RuntimeError):
        with patch.object(handler, "handleError") as he:
            handler.emit(record)
    he.assert_called_once_with(record)
