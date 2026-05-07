import logging
from unittest.mock import patch

import pytest

from observability_library import (
    TracingConfigurationError,
    reset_tracing,
    setup_tracer_provider,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_tracing()
    yield
    reset_tracing()


def _setup(**overrides):
    kwargs = dict(
        service_name="svc",
        environment="dev",
        otlp_endpoint="https://tempo/v1/traces",
        headers={"Authorization": "Bearer x"},
    )
    kwargs.update(overrides)
    return setup_tracer_provider(**kwargs)


def test_https_required_by_default():
    with pytest.raises(TracingConfigurationError, match="https://"):
        _setup(otlp_endpoint="http://tempo/v1/traces")


def test_https_check_can_be_disabled_for_local_dev():
    provider = _setup(
        otlp_endpoint="http://localhost:4318/v1/traces",
        require_tls=False,
        require_auth=False,
        headers=None,
    )
    assert provider is not None


def test_auth_required_by_default():
    with pytest.raises(TracingConfigurationError, match="Authorization"):
        _setup(headers={})


def test_auth_required_when_headers_is_none():
    with pytest.raises(TracingConfigurationError, match="Authorization"):
        _setup(headers=None)


def test_auth_check_is_case_insensitive():
    provider = _setup(headers={"authorization": "Bearer x"})
    assert provider is not None


def test_idempotent_within_process():
    first = _setup()
    second = _setup(service_name="other", environment="prod")
    assert first is second


def test_warns_when_cached_provider_returned_with_different_config(caplog):
    _setup()
    with caplog.at_level(logging.WARNING, logger="observability_library.tracing"):
        _setup(service_name="other", environment="prod")
    assert any("service_name" in r.message for r in caplog.records)
    assert any("environment" in r.message for r in caplog.records)


def test_reset_tracing_allows_new_provider():
    first = _setup()
    reset_tracing()
    second = _setup()
    assert first is not second


def test_reset_tracing_calls_shutdown_on_cached_provider():
    provider = _setup()
    with patch.object(provider, "shutdown") as shutdown:
        reset_tracing()
    shutdown.assert_called_once()


def test_resource_attributes_include_service_and_environment():
    provider = _setup(extra_resource_attributes={"modal.app": "aqua-agent"})
    attrs = provider.resource.attributes
    assert attrs["service.name"] == "svc"
    assert attrs["deployment.environment"] == "dev"
    assert attrs["modal.app"] == "aqua-agent"


def test_reserved_resource_attributes_cannot_be_overridden():
    with pytest.raises(TracingConfigurationError, match="service.name"):
        _setup(extra_resource_attributes={"service.name": "wrong"})
    reset_tracing()
    with pytest.raises(TracingConfigurationError, match="deployment.environment"):
        _setup(extra_resource_attributes={"deployment.environment": "wrong"})


def test_bsp_tuning_parameters_applied_to_processor():
    provider = _setup(
        max_queue_size=1024,
        schedule_delay_millis=100,
        max_export_batch_size=64,
        export_timeout_millis=5000,
    )
    bsp = next(iter(provider._active_span_processor._span_processors))
    inner = bsp._batch_processor
    assert inner._max_queue_size == 1024
    assert inner._schedule_delay_millis == 100
    assert inner._max_export_batch_size == 64
    assert inner._export_timeout_millis == 5000
