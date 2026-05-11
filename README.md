# Observability Library

Python toolkit for shipping structured logs to Loki and traces to an
OTLP backend (Tempo, Grafana Cloud, etc.), with the two correlated for
click-through navigation in Grafana.

## Components

| Symbol | Purpose |
|---|---|
| `LokiHandler` | Async logging handler (aiohttp). Use in FastAPI / asyncio apps. |
| `SyncLokiHandler` | Sync logging handler (requests + queue + worker thread). Use in Django / scripts. |
| `LokiLoggerLabels` | Pydantic schema for typed Loki labels. |
| `setup_tracer_provider` | Configure the global OTel `TracerProvider` for OTLP/HTTP export. |
| `reset_tracing` | Clear the cached provider — for test isolation. |
| `TraceContextFilter` | Stamp `trace_id` / `span_id` / `trace_flags` from the active span onto each `LogRecord`. |
| `TracingConfigurationError` | Raised on misconfiguration (TLS / auth checks, missing extras). |

## Install

```bash
pip install observability-library              # Loki only
pip install observability-library[tracing]     # Loki + OpenTelemetry
```

## Logs to Loki

Async (FastAPI / asyncio):

```python
import logging
from observability_library import LokiHandler

logger = logging.getLogger("my-app")
logger.setLevel(logging.INFO)
logger.addHandler(LokiHandler(
    url="http://localhost:3100/loki/api/v1/push",
    labels={"app": "my-application", "env": "production"},
))

logger.info("login", extra={"user_id": "abc"})
```

Sync (Django / scripts):

```python
from observability_library import SyncLokiHandler

logger.addHandler(SyncLokiHandler(
    url="http://localhost:3100/loki/api/v1/push",
    labels={"app": "my-application"},
))
```

Both handlers share the same constructor surface and ship every
non-standard `LogRecord` attribute as a JSON field. They only differ in
transport.

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
additional span processors. It is idempotent — repeat calls are no-ops
and emit a warning if the new arguments differ from the cached provider.

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
| `Authorization` header must be present and non-empty | on | `require_auth=False` |
| `service.name` / `deployment.environment` cannot be set via `extra_resource_attributes` | enforced | use the named parameters |

All raise `TracingConfigurationError` on failure.

### BatchSpanProcessor tuning

Defaults are chosen for long-running pipelines (hours, thousands of
spans), not bursty web traffic:

| Knob | Default | OTel default |
|---|---|---|
| `max_queue_size` | 8192 | 2048 |
| `schedule_delay_millis` | 2000 | 5000 |
| `max_export_batch_size` | 512 | 512 |
| `export_timeout_millis` | 30000 | 30000 |

Override via keyword arguments to `setup_tracer_provider`.

## Log ↔ trace correlation

```python
from observability_library import TraceContextFilter

logger.addFilter(TraceContextFilter())
```

While a span is active, the filter stamps `trace_id`, `span_id`, and
`trace_flags` onto each `LogRecord`. The Loki handlers forward those
fields to Loki along with anything else attached to the record, so
Grafana derived fields can link any log line to its trace.

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
