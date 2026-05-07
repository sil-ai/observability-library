"""Bridge OpenTelemetry trace context onto Python `logging` records.

Adding a `TraceContextFilter` to a logger (or to a specific handler such
as `LokiHandler` or `SyncLokiHandler`) stamps `trace_id`, `span_id`, and
`trace_flags` onto every record while a span is active. Downstream tools
— Grafana derived fields in particular — can then jump from a log line
to its trace and back.

The filter is a no-op when no span is current, and when the OpenTelemetry
API isn't installed at all, so adding it is safe in environments that
don't yet have tracing wired up.
"""

from __future__ import annotations

import logging


class TraceContextFilter(logging.Filter):
    """Stamp trace_id/span_id/trace_flags from the active OTel span.

    Compatible with any handler that reads those attributes off the
    `LogRecord`. The Loki handlers' default allowlist includes
    `trace_id`, `span_id`, and `trace_flags`, so they are forwarded to
    Loki automatically once this filter is attached.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from opentelemetry import trace
        except ImportError:
            return True

        span = trace.get_current_span()
        ctx = span.get_span_context() if span is not None else None
        if ctx is None or not ctx.is_valid:
            return True

        record.trace_id = format(ctx.trace_id, "032x")
        record.span_id = format(ctx.span_id, "016x")
        record.trace_flags = format(ctx.trace_flags, "02x")
        return True
