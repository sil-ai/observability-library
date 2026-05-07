"""End-to-end example: structured logs to Loki + traces to an OTLP backend.

Requires `pip install observability-library[tracing]` for the OTel pieces.
"""

import logging
import os

from observability_library import (
    LokiHandler,
    TraceContextFilter,
    setup_tracer_provider,
)


def main():
    environment = os.getenv("ENVIRONMENT", "dev")

    setup_tracer_provider(
        service_name="example-app",
        environment=environment,
        otlp_endpoint=os.environ["OTLP_ENDPOINT"],
        headers={"Authorization": f"Bearer {os.environ['OTLP_TOKEN']}"},
        extra_resource_attributes={"team": "platform"},
    )

    logger = logging.getLogger("example-app")
    logger.setLevel(logging.INFO)
    logger.addFilter(TraceContextFilter())

    console = logging.StreamHandler()
    logger.addHandler(console)

    loki = LokiHandler(
        url=os.environ["LOKI_URL"],
        labels={"app": "example-application", "env": environment},
        auth_token=os.getenv("LOKI_TOKEN"),
        extra_allowlist={"assessment_id", "stage"},
    )
    logger.addHandler(loki)

    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("example.run") as span:
        span.set_attribute("aqua.assessment_id", 42)
        logger.info(
            "Run started",
            extra={"assessment_id": 42, "stage": "init"},
        )


if __name__ == "__main__":
    main()
