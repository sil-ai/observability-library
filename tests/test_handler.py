import json
import logging
from unittest.mock import patch

import requests

from observability_library import LokiHandler


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


def test_send_failure_logs_only_exception_class(caplog):
    handler = LokiHandler(url="http://loki:secret@host/push")

    def boom(*a, **kw):
        raise requests.ConnectionError("http://user:pw@host/leaked")

    with patch.object(requests, "post", side_effect=boom):
        with caplog.at_level(logging.ERROR, logger="observability_library.handler"):
            handler.send_to_loki({"streams": []})

    messages = [r.getMessage() for r in caplog.records]
    assert any("ConnectionError" in m for m in messages)
    assert not any("user:pw" in m or "leaked" in m for m in messages)


def test_auth_token_attached_as_bearer():
    handler = LokiHandler(url="http://loki/push", auth_token="t0k3n")
    with patch.object(requests, "post") as post:
        post.return_value.raise_for_status = lambda: None
        handler.send_to_loki({"streams": []})
    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer t0k3n"


def test_emit_routes_through_handle_error_on_format_failure():
    handler = LokiHandler(url="http://loki/push")
    record = _make_record()

    with patch.object(handler, "build_payload", side_effect=RuntimeError):
        with patch.object(handler, "handleError") as he:
            handler.emit(record)
    he.assert_called_once_with(record)
