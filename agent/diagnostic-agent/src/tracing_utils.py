"""
Minimal Trace Context utilities for checkout-demo Lab.

Manual instrumentation — no auto-instrumentation, no body/query/header capture.
All traces are exported to the configured OTLP endpoint (via OTEL_EXPORTER_OTLP_ENDPOINT env).
"""

import os
import re
from typing import Optional, Tuple

from opentelemetry import propagate, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource

# W3C traceparent: 00-<32-hex-trace_id>-<16-hex-span_id>-<2-hex-flags>
_TRACEPARENT_RE = re.compile(
    r'^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$'
)


def _make_resource() -> Resource:
    """Build Resource from OTEL_RESOURCE_ATTRIBUTES and fixed defaults."""
    attrs = {}
    raw = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            attrs[k.strip()] = v.strip()
    return Resource.create(attrs)


_tracer: Optional[trace.Tracer] = None


def get_tracer() -> trace.Tracer:
    """Lazy-init singleton tracer from environment-configured provider."""
    global _tracer
    if _tracer is None:
        resource = _make_resource()
        provider = TracerProvider(resource=resource)

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            exporter = OTLPSpanExporter(endpoint=endpoint)
            processor = BatchSpanProcessor(exporter)
            provider.add_span_processor(processor)

        trace.set_tracer_provider(provider)
        _tracer = provider.get_tracer(__name__)
    return _tracer


def parse_traceparent(header_value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse W3C traceparent header.

    Returns (trace_id, parent_span_id) or (None, None) if absent/invalid.
    Never returns a span context — the SDK span builder reads traceparent from
    the parsed values instead.
    """
    if not header_value:
        return None, None
    m = _TRACEPARENT_RE.match(header_value.strip())
    if not m:
        return None, None
    return m.group(1), m.group(2)


def allowed_span_attributes() -> dict:
    """
    Return the fixed set of allowed Span attributes.

    LAB RULE: never capture request body, query string, Cookie, Authorization,
    user_id, request_id, or any payload field.
    """
    return {
        "http.request.method": "",
        "url.path": "",
        "http.response.status_code": 0,
    }


def start_checkout_span(traceparent: Optional[str]):
    """Create the only instrumented checkout request span.

    A valid W3C traceparent is extracted with the SDK propagator and becomes the
    remote parent. Invalid or absent values intentionally create a new trace.
    Only method and fixed route are recorded here; the HTTP status is set by the
    request handler after it chooses the response.
    """
    carrier = {"traceparent": traceparent} if traceparent else {}
    parent_context = propagate.extract(carrier)
    return get_tracer().start_as_current_span(
        "GET /checkout",
        context=parent_context,
        attributes={
            "http.request.method": "GET",
            "url.path": "/checkout",
        },
    )


def checkout_trace_log_fields(span) -> dict:
    """Return only fixed-width correlation identifiers for JSON logs."""
    context = span.get_span_context()
    return {
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
    }