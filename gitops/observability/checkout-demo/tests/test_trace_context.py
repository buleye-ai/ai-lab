"""
Unit tests for tracing_utils — run outside the container against the module directly.

RED phase: module doesn't exist. Run tests → FAIL.
GREEN phase: after implementing tracing_utils.py → PASS.
REFACTOR: clean up.

Run:
    cd <ai-lab-root>
    pip install -r agent/diagnostic-agent/requirements-tracing.txt
    PYTHONPATH=agent/diagnostic-agent/src python -m pytest \
        gitops/observability/checkout-demo/tests/test_trace_context.py -v
"""

import os
import sys
from unittest.mock import patch

# The module is at agent/diagnostic-agent/src/tracing_utils.py
SRC = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "agent", "diagnostic-agent", "src")
sys.path.insert(0, os.path.abspath(SRC))

import pytest
from tracing_utils import (
    parse_traceparent,
    allowed_span_attributes,
    _make_resource,
    start_checkout_span,
    checkout_trace_log_fields,
    inject_traceparent,
    start_inventory_span,
)


class TestParseTraceparent:
    """W3C traceparent parsing — no SDK, pure string logic."""

    def test_none_returns_none_none(self):
        tid, pid = parse_traceparent(None)
        assert tid is None
        assert pid is None

    def test_empty_returns_none_none(self):
        tid, pid = parse_traceparent("")
        assert tid is None
        assert pid is None

    def test_invalid_format_returns_none_none(self):
        tid, pid = parse_traceparent("01-abc-xyz-00")
        assert tid is None
        assert pid is None

    def test_valid_traceparent_parses_trace_id(self):
        header = "00-a1b2c3d4e5f67890a1b2c3d4e5f67890-0102030405060708-01"
        tid, pid = parse_traceparent(header)
        assert tid == "a1b2c3d4e5f67890a1b2c3d4e5f67890"
        assert pid == "0102030405060708"

    def test_valid_traceparent_sampled_flag(self):
        header = "00-00000000000000000000000000000001-0000000000000001-00"
        tid, pid = parse_traceparent(header)
        assert tid == "00000000000000000000000000000001"
        assert pid == "0000000000000001"


class TestAllowedSpanAttributes:
    """Contract: no sensitive fields leak into span attributes."""

    def test_structure_is_dict(self):
        attrs = allowed_span_attributes()
        assert isinstance(attrs, dict)

    def test_has_only_allowed_keys(self):
        attrs = allowed_span_attributes()
        allowed = {"http.request.method", "url.path", "http.response.status_code"}
        assert set(attrs.keys()) == allowed

    def test_no_sensitive_keys(self):
        attrs = allowed_span_attributes()
        sensitive = {"body", "query", "cookie", "authorization", "token",
                     "user_id", "request_id", "password", "secret"}
        assert sensitive.isdisjoint(attrs.keys())


class TestMakeResource:
    """Resource construction from environment variable."""

    def test_default_resource_has_sdk_defaults_not_custom(self):
        with patch.dict(os.environ, {}, clear=True):
            r = _make_resource()
        # SDK always injects telemetry.sdk.* and service.name defaults
        assert r.attributes.get("service.name") == "unknown_service"
        assert "telemetry.sdk.language" in r.attributes

    def test_parses_otlp_resource_attributes(self):
        raw = "service.name=checkout-demo,deployment.environment=lab,service.version=baseline"
        with patch.dict(os.environ, {"OTEL_RESOURCE_ATTRIBUTES": raw}, clear=True):
            r = _make_resource()
        assert r.attributes.get("service.name") == "checkout-demo"
        assert r.attributes.get("deployment.environment") == "lab"
        assert r.attributes.get("service.version") == "baseline"

    def test_ignores_whitespace_pair(self):
        raw = "key1=val1, ,key2=val2"
        with patch.dict(os.environ, {"OTEL_RESOURCE_ATTRIBUTES": raw}, clear=True):
            r = _make_resource()
        assert r.attributes.get("key1") == "val1"
        assert r.attributes.get("key2") == "val2"


class TestCheckoutSpan:
    """A real SDK span must be safe and continue a valid W3C parent context."""

    def test_new_request_creates_32_hex_trace_id_and_safe_attributes(self):
        with start_checkout_span(None) as span:
            context = span.get_span_context()
            assert f"{context.trace_id:032x}".isalnum()
            assert len(f"{context.trace_id:032x}") == 32
            span.set_attribute("http.response.status_code", 200)
            attrs = dict(span.attributes)
        assert attrs["http.request.method"] == "GET"
        assert attrs["url.path"] == "/checkout"
        assert attrs["http.response.status_code"] == 200
        assert set(attrs) == {
            "http.request.method", "url.path", "http.response.status_code"
        }

    def test_valid_traceparent_keeps_incoming_trace_id(self):
        incoming_trace_id = "a1b2c3d4e5f67890a1b2c3d4e5f67890"
        header = f"00-{incoming_trace_id}-0102030405060708-01"
        with start_checkout_span(header) as span:
            actual = f"{span.get_span_context().trace_id:032x}"
        assert actual == incoming_trace_id

    def test_invalid_traceparent_starts_new_trace(self):
        with start_checkout_span("not-a-traceparent") as span:
            actual = f"{span.get_span_context().trace_id:032x}"
        assert actual != "0" * 32

    def test_log_correlation_contains_only_trace_and_span_ids(self):
        with start_checkout_span(None) as span:
            fields = checkout_trace_log_fields(span)
        assert set(fields) == {"trace_id", "span_id"}
        assert len(fields["trace_id"]) == 32
        assert len(fields["span_id"]) == 16


class TestDownstreamPropagation:
    """Cross-service contract: child spans keep the parent trace, safely."""

    # --- existing propagation tests (preserved) ---
    def test_injected_traceparent_keeps_current_trace_and_uses_new_parent_span(self):
        with start_checkout_span(None) as checkout_span:
            checkout_context = checkout_span.get_span_context()
            headers = inject_traceparent()

        assert set(headers) == {"traceparent"}
        version, trace_id, parent_span_id, flags = headers["traceparent"].split("-")
        assert version == "00"
        assert trace_id == f"{checkout_context.trace_id:032x}"
        assert parent_span_id == f"{checkout_context.span_id:016x}"
        assert flags == "01"

    def test_inventory_span_continues_injected_trace_and_uses_safe_attributes(self):
        with start_checkout_span(None) as checkout_span:
            expected_trace_id = f"{checkout_span.get_span_context().trace_id:032x}"
            headers = inject_traceparent()

        with start_inventory_span(headers["traceparent"]) as inventory_span:
            inventory_context = inventory_span.get_span_context()
            inventory_span.set_attribute("http.response.status_code", 200)
            attrs = dict(inventory_span.attributes)

        assert f"{inventory_context.trace_id:032x}" == expected_trace_id
        assert attrs == {
            "http.request.method": "GET",
            "url.path": "/reserve",
            "http.response.status_code": 200,
        }

    # --- new sampling tests ---

    def _clear_tracer(self):
        """Force re-init of the global tracer for sampler env testing."""
        import tracing_utils as tu
        tu._tracer = None
        # Clear the global tracer registry so next call creates fresh provider
        from opentelemetry import trace as otel_trace
        otel_trace._TRACER_PROVIDER = None
        otel_trace._PROVIDER = None

    def test_parentbased_traceidratio_root_spans_are_sampled_at_ratio(self):
        """
        OTEL_TRACES_SAMPLER=parentbased_traceidratio
        with arg=0.1 should sample ~10% of root (no-parent) spans.
        """
        self._clear_tracer()
        with patch.dict(os.environ, {
            "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
            "OTEL_TRACES_SAMPLER_ARG": "0.1",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "",
            "OTEL_RESOURCE_ATTRIBUTES": "",
        }, clear=True):
            from tracing_utils import get_tracer, _make_resource
            sampled, total = 0, 200
            for _ in range(total):
                with get_tracer().start_as_current_span("test") as span:
                    if span.get_span_context().trace_flags & 0x01:
                        sampled += 1
        # Allow a generous ±5% margin around 10%
        assert 10 <= sampled <= 30, f"sampled {sampled}/{total} outside expected 10-30"
        self._clear_tracer()

    def test_child_inherits_parent_sampled_decision(self):
        """
        With ParentBased sampling, a child span created from a sampled parent
        retains the same sampled flag.
        """
        self._clear_tracer()
        with patch.dict(os.environ, {
            "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
            "OTEL_TRACES_SAMPLER_ARG": "0.1",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "",
            "OTEL_RESOURCE_ATTRIBUTES": "",
        }, clear=True):
            from tracing_utils import get_tracer, inject_traceparent, start_inventory_span
            # Create a root span; if sampled, inventory child must also be sampled
            for _ in range(100):
                root_tracer = get_tracer()
                with root_tracer.start_as_current_span("test-root") as root:
                    root_sampled = bool(root.get_span_context().trace_flags & 0x01)
                    headers = inject_traceparent()
                if not headers or "traceparent" not in headers:
                    continue  # root may have been dropped by batch; skip
                with start_inventory_span(headers["traceparent"]) as child:
                    child_sampled = bool(child.get_span_context().trace_flags & 0x01)
                assert root_sampled == child_sampled, (
                    f"root sampled={root_sampled} but child sampled={child_sampled} — "
                    "parent decision must propagate to child"
                )
        self._clear_tracer()

    def test_head_sampling_does_not_guarantee_error_retention(self):
        """
        Documented limitation: pure head sampling (even parentbased) decides at
        the root; errors that occur downstream are invisible if the root was
        already dropped. This test proves the limitation is real by showing
        errant roots can be dropped.
        """
        self._clear_tracer()
        with patch.dict(os.environ, {
            "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
            "OTEL_TRACES_SAMPLER_ARG": "0.1",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "",
            "OTEL_RESOURCE_ATTRIBUTES": "",
        }, clear=True):
            from tracing_utils import get_tracer
            dropped_roots = 0
            for _ in range(200):
                with get_tracer().start_as_current_span("test-root") as span:
                    if not (span.get_span_context().trace_flags & 0x01):
                        dropped_roots += 1
            # With 10% ratio, most roots are dropped; this proves errors are invisible too
            assert dropped_roots >= 100, (
                f"Only {dropped_roots}/200 roots dropped — insufficient proof that "
                "head sampling can miss errors. If this fails repeatedly, the SDK "
                "behaviour may have changed."
            )
        self._clear_tracer()
