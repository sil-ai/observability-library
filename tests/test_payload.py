import json

from observability_library._payload import build_loki_payload

from ._helpers import make_record


def _decode(payload):
    return json.loads(payload["streams"][0]["values"][0][1])


def test_payload_contains_canonical_body_fields():
    payload = build_loki_payload(make_record(), "hello world", {})
    body = _decode(payload)
    assert body["level"] == "INFO"
    assert body["logger"] == "test"
    assert body["message"] == "hello world"


def test_payload_propagates_labels_verbatim():
    labels = {"app": "x", "env": "prod", "service": "api"}
    payload = build_loki_payload(make_record(), "m", labels)
    assert payload["streams"][0]["stream"] == labels


def test_payload_uses_nanosecond_epoch_timestamp():
    payload = build_loki_payload(make_record(), "m", {})
    timestamp = payload["streams"][0]["values"][0][0]
    assert timestamp.isdigit()
    assert int(timestamp) > 1_000_000_000_000_000_000


def test_payload_forwards_extras_attached_to_record():
    record = make_record(user_id="abc", request_id="r-1")
    body = _decode(build_loki_payload(record, "m", {}))
    assert body["user_id"] == "abc"
    assert body["request_id"] == "r-1"


def test_payload_forwards_trace_context_when_present():
    record = make_record(trace_id="a" * 32, span_id="b" * 16, trace_flags="01")
    body = _decode(build_loki_payload(record, "m", {}))
    assert body["trace_id"] == "a" * 32
    assert body["span_id"] == "b" * 16
    assert body["trace_flags"] == "01"


def test_payload_drops_standard_logrecord_attrs():
    # Standard attrs like `args`, `msg`, `pathname` etc. should never
    # appear in the body — they're internals.
    body = _decode(build_loki_payload(make_record(), "m", {}))
    assert "args" not in body
    assert "msg" not in body
    assert "pathname" not in body
    assert "thread" not in body


def test_payload_drops_underscore_prefixed_attrs():
    record = make_record(_internal="should-not-ship", visible="yes")
    body = _decode(build_loki_payload(record, "m", {}))
    assert "_internal" not in body
    assert body["visible"] == "yes"


def test_payload_forwards_falsy_values():
    record = make_record(count=0, flag=False, empty="")
    body = _decode(build_loki_payload(record, "m", {}))
    assert body["count"] == 0
    assert body["flag"] is False
    assert body["empty"] == ""


def test_payload_handles_non_serialisable_extras_via_default_str():
    class Custom:
        def __str__(self):
            return "custom-repr"

    record = make_record(obj=Custom())
    body = _decode(build_loki_payload(record, "m", {}))
    assert body["obj"] == "custom-repr"


def test_payload_survives_extras_with_raising_str():
    # A buggy __str__ used to propagate out of build_loki_payload via
    # json.dumps(default=str), which would silently drop the entire
    # record in handleError. The fallback path replaces just the bad
    # field so the rest of the body still ships.
    class Hostile:
        def __str__(self):
            raise RuntimeError("boom")

        def __repr__(self):
            return "Hostile()"

    record = make_record(obj=Hostile())
    body = _decode(build_loki_payload(record, "m", {}))
    assert body["level"] == "INFO"
    assert body["message"] == "m"
    assert body["obj"] == "Hostile()"


def test_payload_handles_empty_labels():
    payload = build_loki_payload(make_record(), "m", {})
    assert payload["streams"][0]["stream"] == {}
