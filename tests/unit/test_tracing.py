import re

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from salience.observability.tracing import OpenTelemetryTraceEmitter, TraceContext


def test_trace_context_is_w3c_compatible_and_emits_matching_otel_trace() -> None:
    root = TraceContext.new_root()
    child = root.new_child()

    assert re.fullmatch(r"[0-9a-f]{32}", root.trace_id)
    assert re.fullmatch(r"[0-9a-f]{16}", child.span_id)
    assert TraceContext.from_carrier(child.to_carrier()) == child

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    emitter = OpenTelemetryTraceEmitter(provider.get_tracer("salience.test"))

    emitter.emit(child, "governance.authorize")
    span = exporter.get_finished_spans()[0]
    assert f"{span.context.trace_id:032x}" == child.trace_id
    assert span.attributes["salience.span_id"] == child.span_id

