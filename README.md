# Observability Library

Python toolkit for shipping structured logs to Loki and traces to an
OTLP backend (Tempo, Grafana Cloud, etc.), with the two correlated for
click-through navigation in Grafana.

## Install

```bash
pip install observability-library              # Loki only
pip install observability-library[tracing]     # Loki + OpenTelemetry tracing
```

## Logs to Loki

```python
import logging
from observability_library import LokiHandler

logger = logging.getLogger("my-app")
logger.setLevel(logging.INFO)
logger.addHandler(LokiHandler(
    url="http://localhost:3100/loki/api/v1/push",
    labels={"app": "my-application", "env": "production"},
    extra_allowlist={"user_id", "request_id"},
))

logger.info("login", extra={"user_id": "abc"})
```

### Allowlist for `extra` fields

`LokiHandler` only forwards record attributes whose keys are in
`extra_allowlist`. The default allowlist covers OpenTelemetry
correlation fields (`trace_id`, `span_id`, `trace_flags`); extend it
explicitly for any application-specific fields you want shipped to Loki.
This prevents secrets or large payloads from being attached to records
elsewhere in the codebase from leaking into Loki.

## Tracing

```python
from observability_library import setup_tracer_provider

setup_tracer_provider(
    service_name="my-service",
    environment="prod",
    otlp_endpoint="https://tempo.example.com/v1/traces",
    headers={"Authorization": "Bearer ..."},
)
```

`setup_tracer_provider` registers a global `TracerProvider` with a
batched OTLP/HTTP exporter and returns it so callers can attach
additional span processors. It is idempotent — repeat calls are no-ops.

The library does **not** call any auto-instrumentation. Each service
chooses what to instrument; for example, an LLM agent might do:

```python
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from openinference.instrumentation.openai import OpenAIInstrumentor

RequestsInstrumentor().instrument()
BotocoreInstrumentor().instrument()
OpenAIInstrumentor().instrument()
```

### Security defaults

| Check | Default | Override |
|---|---|---|
| Endpoint must be `https://` | on | `require_tls=False` |
| `Authorization` header must be present | on | `require_auth=False` |

Both raise `TracingConfigurationError` on failure.

### BatchSpanProcessor tuning

Defaults are chosen for long-running pipelines (hours, thousands of
spans), not bursty web traffic:

| Knob | Default | OTel default |
|---|---|---|
| `max_queue_size` | 8192 | 2048 |
| `schedule_delay_millis` | 2000 | 5000 |
| `max_export_batch_size` | 512 | 512 |

Override via keyword arguments to `setup_tracer_provider`.

## Log ↔ trace correlation

```python
from observability_library import TraceContextFilter

logger.addFilter(TraceContextFilter())
```

While a span is active, the filter stamps `trace_id`, `span_id`, and
`trace_flags` onto each `LogRecord`. `LokiHandler`'s default allowlist
forwards them to Loki, so Grafana derived fields can link any log line
to its trace.

## Testing

`reset_tracing()` clears the cached `TracerProvider` for test isolation.
A typical pytest fixture:

```python
import pytest
from observability_library import reset_tracing

@pytest.fixture(autouse=True)
def _reset_tracing():
    yield
    reset_tracing()
```

## License

MIT
