import json

import pytest

from observability_library import DEFAULT_EXTRA_ALLOWLIST
from observability_library._payload import (
    build_loki_payload,
    validate_allowlist,
)

from ._helpers import make_record


# ---- validate_allowlist ----------------------------------------------------


def test_validate_allowlist_with_none_returns_defaults():
    result = validate_allowlist(None)
    assert result == DEFAULT_EXTRA_ALLOWLIST


def test_validate_allowlist_unions_with_defaults():
    result = validate_allowlist({"assessment_id", "stage"})
    assert "assessment_id" in result
    assert "stage" in result
    for key in DEFAULT_EXTRA_ALLOWLIST:
        assert key in result


def test_validate_allowlist_rejects_standard_record_attrs():
    with pytest.raises(ValueError, match="standard LogRecord"):
        validate_allowlist({"args"})
    with pytest.raises(ValueError, match="standard LogRecord"):
        validate_allowlist({"msg"})


def test_validate_allowlist_rejects_reserved_body_keys():
    with pytest.raises(ValueError, match="reserved body keys"):
        validate_allowlist({"level"})
    with pytest.raises(ValueError, match="reserved body keys"):
        validate_allowlist({"logger"})


def test_validate_allowlist_accepts_iterables():
    # frozenset, list, tuple all valid
    assert "x" in validate_allowlist(frozenset({"x"}))
    assert "x" in validate_allowlist(["x"])
    assert "x" in validate_allowlist(("x",))


# ---- build_loki_payload ----------------------------------------------------


def _decode(payload):
    return json.loads(payload["streams"][0]["values"][0][1])


def test_payload_contains_canonical_body_fields():
    payload = build_loki_payload(make_record(), "hello world", {}, validate_allowlist(None))
    body = _decode(payload)
    assert body["level"] == "INFO"
    assert body["logger"] == "test"
    assert body["message"] == "hello world"


def test_payload_propagates_labels_verbatim():
    labels = {"app": "x", "env": "prod", "service": "api"}
    payload = build_loki_payload(make_record(), "m", labels, validate_allowlist(None))
    assert payload["streams"][0]["stream"] == labels


def test_payload_uses_nanosecond_epoch_timestamp():
    payload = build_loki_payload(make_record(), "m", {}, validate_allowlist(None))
    timestamp = payload["streams"][0]["values"][0][0]
    assert timestamp.isdigit()
    assert int(timestamp) > 1_000_000_000_000_000_000


def test_payload_includes_trace_context_by_default():
    record = make_record(trace_id="a" * 32, span_id="b" * 16, trace_flags="01")
    payload = build_loki_payload(record, "m", {}, validate_allowlist(None))
    body = _decode(payload)
    assert body["trace_id"] == "a" * 32
    assert body["span_id"] == "b" * 16
    assert body["trace_flags"] == "01"


def test_payload_filters_unallowlisted_fields():
    allowlist = validate_allowlist({"assessment_id"})
    record = make_record(assessment_id=42, secret_token="leak")
    body = _decode(build_loki_payload(record, "m", {}, allowlist))
    assert body["assessment_id"] == 42
    assert "secret_token" not in body


def test_payload_forwards_falsy_values_but_drops_none():
    allowlist = validate_allowlist({"count", "flag", "empty", "missing"})
    record = make_record(count=0, flag=False, empty="")
    body = _decode(build_loki_payload(record, "m", {}, allowlist))
    assert body["count"] == 0
    assert body["flag"] is False
    assert body["empty"] == ""
    assert "missing" not in body


def test_payload_handles_non_serialisable_extras_via_default_str():
    class Custom:
        def __str__(self):
            return "custom-repr"

    allowlist = validate_allowlist({"obj"})
    record = make_record(obj=Custom())
    body = _decode(build_loki_payload(record, "m", {}, allowlist))
    assert body["obj"] == "custom-repr"
