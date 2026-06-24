"""Send-failure logging guarantees, asserted directly on the helper.

Both `LokiHandler` and `SyncLokiHandler` route their send failures
through `log_send_failure`. Verifying the contract once here is
sufficient — the handler tests then need only assert that they call it.
"""

import logging
from unittest.mock import patch

import requests

from observability_library._payload import _send_failure_logger, log_send_failure


def test_logs_only_exception_class_not_message():
    with patch.object(_send_failure_logger, "error") as err:
        log_send_failure("sync", requests.ConnectionError("http://user:pw@host/leaked"))

    err.assert_called_once()
    fmt, *args = err.call_args.args
    rendered = fmt % tuple(args)
    assert "ConnectionError" in rendered
    assert "user:pw" not in rendered
    assert "leaked" not in rendered


def test_handler_kind_label_propagated():
    with patch.object(_send_failure_logger, "error") as err:
        log_send_failure("async", RuntimeError("x"))
    fmt, *args = err.call_args.args
    rendered = fmt % tuple(args)
    assert "async" in rendered

    with patch.object(_send_failure_logger, "error") as err:
        log_send_failure("sync", RuntimeError("x"))
    fmt, *args = err.call_args.args
    rendered = fmt % tuple(args)
    assert "sync" in rendered


def test_internal_logger_does_not_propagate():
    # Recursion guard: if root logger has a LokiHandler attached, an emit
    # failure would re-enter emit() via propagation.
    assert _send_failure_logger.propagate is False


def test_internal_logger_uses_dedicated_name():
    # Documented contract — consumers can subscribe to this logger.
    assert _send_failure_logger.name == "observability_library._payload"


def test_logging_at_error_level():
    with patch.object(_send_failure_logger, "error") as err:
        log_send_failure("async", RuntimeError("x"))
    err.assert_called_once()
    # We only call .error — never .warning, .info, etc.
    assert isinstance(_send_failure_logger, logging.Logger)


def test_loki_debug_env_var_includes_message(monkeypatch):
    monkeypatch.setenv("LOKI_DEBUG", "1")
    with patch.object(_send_failure_logger, "error") as err:
        log_send_failure("async", TimeoutError("Connection timed out after 30s"))
    fmt, *args = err.call_args.args
    rendered = fmt % tuple(args)
    assert "TimeoutError" in rendered
    assert "Connection timed out after 30s" in rendered


def test_loki_debug_env_var_strips_basic_auth_in_url(monkeypatch):
    monkeypatch.setenv("LOKI_DEBUG", "1")
    with patch.object(_send_failure_logger, "error") as err:
        log_send_failure(
            "sync",
            requests.ConnectionError(
                "HTTPSConnectionPool(host='loki.example', port=443): "
                "https://user:secret@loki.example/loki/api/v1/push"
            ),
        )
    fmt, *args = err.call_args.args
    rendered = fmt % tuple(args)
    assert "user:secret" not in rendered
    assert "secret" not in rendered
    assert "loki.example" in rendered


def test_loki_debug_off_by_default():
    # Unset to be safe in case other tests left it set.
    import os
    original = os.environ.pop("LOKI_DEBUG", None)
    try:
        with patch.object(_send_failure_logger, "error") as err:
            log_send_failure("async", TimeoutError("Connection timed out"))
        fmt, *args = err.call_args.args
        rendered = fmt % tuple(args)
        assert "Connection timed out" not in rendered
        assert "TimeoutError" in rendered
    finally:
        if original is not None:
            os.environ["LOKI_DEBUG"] = original
