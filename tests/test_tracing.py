import pytest

from observability_library import (
    TracingConfigurationError,
    reset_tracing,
    setup_tracer_provider,
)


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_tracing()


def test_https_required_by_default():
    with pytest.raises(TracingConfigurationError, match="https://"):
        setup_tracer_provider(
            service_name="svc",
            environment="dev",
            otlp_endpoint="http://tempo/v1/traces",
            headers={"Authorization": "Bearer x"},
        )


def test_https_check_can_be_disabled_for_local_dev():
    provider = setup_tracer_provider(
        service_name="svc",
        environment="dev",
        otlp_endpoint="http://localhost:4318/v1/traces",
        require_tls=False,
        require_auth=False,
    )
    assert provider is not None


def test_auth_required_by_default():
    with pytest.raises(TracingConfigurationError, match="Authorization"):
        setup_tracer_provider(
            service_name="svc",
            environment="dev",
            otlp_endpoint="https://tempo/v1/traces",
            headers={},
        )


def test_auth_check_is_case_insensitive():
    provider = setup_tracer_provider(
        service_name="svc",
        environment="dev",
        otlp_endpoint="https://tempo/v1/traces",
        headers={"authorization": "Bearer x"},
    )
    assert provider is not None


def test_idempotent_within_process():
    first = setup_tracer_provider(
        service_name="svc",
        environment="dev",
        otlp_endpoint="https://tempo/v1/traces",
        headers={"Authorization": "Bearer x"},
    )
    second = setup_tracer_provider(
        service_name="other",
        environment="prod",
        otlp_endpoint="https://different/v1/traces",
        headers={"Authorization": "Bearer y"},
    )
    assert first is second


def test_reset_tracing_allows_new_provider():
    first = setup_tracer_provider(
        service_name="svc",
        environment="dev",
        otlp_endpoint="https://tempo/v1/traces",
        headers={"Authorization": "Bearer x"},
    )
    reset_tracing()
    second = setup_tracer_provider(
        service_name="svc",
        environment="dev",
        otlp_endpoint="https://tempo/v1/traces",
        headers={"Authorization": "Bearer x"},
    )
    assert first is not second


def test_resource_attributes_include_service_and_environment():
    provider = setup_tracer_provider(
        service_name="aqua-agent",
        environment="prod",
        otlp_endpoint="https://tempo/v1/traces",
        headers={"Authorization": "Bearer x"},
        extra_resource_attributes={"modal.app": "aqua-agent"},
    )
    attrs = provider.resource.attributes
    assert attrs["service.name"] == "aqua-agent"
    assert attrs["deployment.environment"] == "prod"
    assert attrs["modal.app"] == "aqua-agent"
