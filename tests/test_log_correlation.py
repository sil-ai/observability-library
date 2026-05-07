import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from observability_library import TraceContextFilter, reset_tracing


@pytest.fixture
def tracer():
    provider = TracerProvider()
    trace.set_tracer_provider(provider)
    yield provider.get_tracer("test")
    reset_tracing()


def _make_record():
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="m",
        args=(),
        exc_info=None,
    )


def test_filter_is_noop_without_active_span():
    filter_ = TraceContextFilter()
    record = _make_record()
    assert filter_.filter(record) is True
    assert not hasattr(record, "trace_id")
    assert not hasattr(record, "span_id")


def test_filter_stamps_ids_when_span_active(tracer):
    filter_ = TraceContextFilter()
    with tracer.start_as_current_span("s"):
        record = _make_record()
        filter_.filter(record)

    assert len(record.trace_id) == 32
    assert len(record.span_id) == 16
    assert int(record.trace_id, 16) != 0
    assert int(record.span_id, 16) != 0


def test_filter_returns_true_so_record_is_emitted(tracer):
    filter_ = TraceContextFilter()
    with tracer.start_as_current_span("s"):
        assert filter_.filter(_make_record()) is True
