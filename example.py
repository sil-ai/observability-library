"""End-to-end example: logs to Loki + traces to an OTLP backend.

Two flavours below — pick the handler that matches your runtime:
  - `LokiHandler` for async (FastAPI / asyncio)
  - `SyncLokiHandler` for sync (Django / scripts)

Requires `pip install observability-library[tracing]` for the OTel pieces.
"""

import asyncio
import logging
import os

from observability_library import (
    LokiHandler,
    SyncLokiHandler,
    TraceContextFilter,
    setup_tracer_provider,
)


def _configure_logger(handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger("example-app")
    logger.setLevel(logging.INFO)
    logger.addFilter(TraceContextFilter())
    logger.addHandler(logging.StreamHandler())
    logger.addHandler(handler)
    return logger


def _setup_tracing(environment: str) -> None:
    setup_tracer_provider(
        service_name="example-app",
        environment=environment,
        otlp_endpoint=os.environ["OTLP_ENDPOINT"],
        headers={"Authorization": f"Bearer {os.environ['OTLP_TOKEN']}"},
        extra_resource_attributes={"team": "platform"},
    )


async def async_example() -> None:
    environment = os.getenv("ENVIRONMENT", "dev")
    _setup_tracing(environment)

    logger = _configure_logger(LokiHandler(
        url=os.environ["LOKI_URL"],
        labels={"app": "example-application", "env": environment},
        auth_token=os.getenv("LOKI_TOKEN"),
        extra_allowlist={"assessment_id", "stage"},
    ))

    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("example.async_run") as span:
        span.set_attribute("aqua.assessment_id", 42)
        logger.info("Run started", extra={"assessment_id": 42, "stage": "init"})


def sync_example() -> None:
    environment = os.getenv("ENVIRONMENT", "dev")
    _setup_tracing(environment)

    logger = _configure_logger(SyncLokiHandler(
        url=os.environ["LOKI_URL"],
        labels={"app": "example-application", "env": environment},
        auth_token=os.getenv("LOKI_TOKEN"),
        extra_allowlist={"job_id"},
    ))

    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("example.sync_run"):
        logger.info("Job started", extra={"job_id": "abc"})


if __name__ == "__main__":
    if os.getenv("EXAMPLE_MODE", "async") == "sync":
        sync_example()
    else:
        asyncio.run(async_example())
