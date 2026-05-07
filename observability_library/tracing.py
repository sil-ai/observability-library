"""Provider-agnostic OpenTelemetry tracer setup.

This module is intentionally narrow: it builds a `TracerProvider`, wires it
to an OTLP/HTTP exporter, registers it as the global tracer provider, and
returns it. It does *not* call any `*Instrumentor().instrument()` — that
belongs to the application, since the choice of which libraries to
auto-instrument is service-specific.

Security defaults:
  - The OTLP endpoint must use https://. Override with `require_tls=False`
    for local dev (e.g. http://localhost:4318/v1/traces).
  - The exporter headers must include an Authorization header. Override
    with `require_auth=False` for self-hosted deployments where the
    network is the trust boundary.

BatchSpanProcessor defaults are tuned for long-running workloads (hours,
thousands of spans) rather than typical web-service request bursts. See
the parameter docstrings for the rationale.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict, Optional


if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider


_RESERVED_RESOURCE_ATTRS = frozenset({"service.name", "deployment.environment"})


_provider: Optional["TracerProvider"] = None
_provider_lock = threading.Lock()
_logger = logging.getLogger(__name__)


class TracingConfigurationError(ValueError):
    """Raised when the tracing setup arguments fail a security check."""


def setup_tracer_provider(
    service_name: str,
    environment: str,
    otlp_endpoint: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    extra_resource_attributes: Optional[Dict[str, Any]] = None,
    require_tls: bool = True,
    require_auth: bool = True,
    max_queue_size: int = 8192,
    schedule_delay_millis: int = 2000,
    max_export_batch_size: int = 512,
    export_timeout_millis: int = 30000,
) -> "TracerProvider":
    """Configure and register a global TracerProvider for OTLP/HTTP export.

    Idempotent: subsequent calls return the cached provider. A warning
    is emitted if a later call passes a different `service_name`,
    `environment`, or `otlp_endpoint` so a misconfigured second call
    cannot silently shadow a correctly-configured first call.

    Use `reset_tracing()` in tests to clear the cached provider.

    Args:
        service_name: Logical service identifier (becomes `service.name`
            on every span). Should match what dashboards filter on.
        environment: Deployment environment (`dev`, `prod`, etc.) — set
            as `deployment.environment` on the resource.
        otlp_endpoint: Full OTLP traces endpoint, including the
            `/v1/traces` suffix (e.g. `https://tempo.example.com/v1/traces`).
        headers: Extra HTTP headers for the exporter. Typically used to
            pass `Authorization: Bearer <token>`.
        extra_resource_attributes: Additional `resource.attributes`
            (e.g. `{"modal.app": "aqua-agent"}`). Cannot include
            `service.name` or `deployment.environment` — those come
            from the explicit named parameters and overlap raises
            `TracingConfigurationError`.
        require_tls: When True (default) reject endpoints that don't
            start with `https://`. The OTel SDK does not enforce TLS.
        require_auth: When True (default) reject configurations that
            omit an Authorization header. Empty auth silently downgrades
            to anonymous, which can be either a 401 dead-end or, on a
            self-hosted instance with no auth required, a data leak.
        max_queue_size: BatchSpanProcessor queue depth. Default is 8192
            (vs. the OTel default of 2048) because long-running pipelines
            with auto-instrumented LLM calls can emit many spans between
            export ticks.
        schedule_delay_millis: Maximum time spans wait in the queue
            before export. Default is 2000ms (vs. OTel default 5000ms)
            to shrink the data-loss window if the host is preempted.
        max_export_batch_size: Max spans per export request. Conservative
            default of 512 keeps individual requests cheap on small
            self-hosted Tempo instances.
        export_timeout_millis: Per-export HTTP timeout. 30s leaves
            headroom for transient backend slowness without hanging the
            BSP worker indefinitely.

    Returns:
        The registered `TracerProvider`. Callers can attach additional
        span processors (e.g. a `SimpleSpanProcessor` for orchestrator
        root spans where synchronous export is preferable).

    Raises:
        TracingConfigurationError: If `require_tls` or `require_auth` is
            set and the corresponding check fails, or if
            `extra_resource_attributes` overlaps the reserved keys.
    """
    global _provider

    if extra_resource_attributes:
        overlap = _RESERVED_RESOURCE_ATTRS & extra_resource_attributes.keys()
        if overlap:
            raise TracingConfigurationError(
                "extra_resource_attributes cannot override "
                f"{sorted(overlap)}; use the explicit parameters instead."
            )

    with _provider_lock:
        if _provider is not None:
            cached_attrs = _provider.resource.attributes
            mismatches = []
            if cached_attrs.get("service.name") != service_name:
                mismatches.append("service_name")
            if cached_attrs.get("deployment.environment") != environment:
                mismatches.append("environment")
            cached_endpoint = getattr(_provider, "_observability_otlp_endpoint", None)
            if cached_endpoint is not None and cached_endpoint != otlp_endpoint:
                mismatches.append("otlp_endpoint")
            if mismatches:
                _logger.warning(
                    "setup_tracer_provider called again with different %s; "
                    "returning the cached provider. Use reset_tracing() if "
                    "you need to reconfigure.",
                    ", ".join(mismatches),
                )
            return _provider

        if require_tls and not otlp_endpoint.startswith("https://"):
            raise TracingConfigurationError(
                f"OTLP endpoint must use https:// (got {otlp_endpoint!r}). "
                "Pass require_tls=False to override for local development."
            )

        if require_auth:
            has_auth = headers is not None and any(
                k.lower() == "authorization" for k in headers
            )
            if not has_auth:
                raise TracingConfigurationError(
                    "OTLP exporter is missing an Authorization header. "
                    "Pass require_auth=False to override for self-hosted "
                    "deployments where the network is the trust boundary."
                )

        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError as e:
            raise TracingConfigurationError(
                "OpenTelemetry packages are not installed. "
                "Install with `pip install observability-library[tracing]`."
            ) from e

        resource_attrs: Dict[str, Any] = dict(extra_resource_attributes or {})
        resource_attrs["service.name"] = service_name
        resource_attrs["deployment.environment"] = environment

        provider = TracerProvider(resource=Resource.create(resource_attrs))
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=otlp_endpoint, headers=headers or {}),
                max_queue_size=max_queue_size,
                schedule_delay_millis=schedule_delay_millis,
                max_export_batch_size=max_export_batch_size,
                export_timeout_millis=export_timeout_millis,
            )
        )

        trace.set_tracer_provider(provider)
        # Stash on the provider so the idempotency-mismatch warning can
        # detect endpoint changes on subsequent calls (resource attrs do
        # not include the OTLP endpoint).
        provider._observability_otlp_endpoint = otlp_endpoint
        _provider = provider
        return provider


def reset_tracing() -> None:
    """Clear the cached provider so `setup_tracer_provider` re-runs.

    Calls `provider.shutdown()` on the cached provider so its BSP
    worker thread terminates cleanly. Note that the OTel API global
    still points to the (now shut-down) provider until the next
    `setup_tracer_provider` call replaces it — do not record spans in
    the gap.

    Intended for test isolation. Production code should not call this.
    """
    global _provider
    with _provider_lock:
        if _provider is not None:
            try:
                _provider.shutdown()
            except Exception:
                pass
        _provider = None
