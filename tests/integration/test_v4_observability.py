import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from salience.observability.tracing import OpenTelemetryTraceEmitter, TraceContext


@pytest.mark.parametrize("traceparent", ["00-" + "0" * 32 + "-1234567890123456-01", "00-" + "1" * 32 + "-0000000000000000-01", "00-" + "A" * 32 + "-1234567890123456-01"])
def test_invalid_w3c_identity_is_not_propagated(traceparent):
    with pytest.raises(ValueError):
        TraceContext.from_carrier({"traceparent": traceparent})


def test_existing_activity_adapter_trace_exports_canonical_identity_without_secrets():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    emitter = OpenTelemetryTraceEmitter(provider.get_tracer("p0-test"))
    root = TraceContext.new_root()
    activity = TraceContext.from_carrier(root.to_carrier()).new_child()
    adapter = TraceContext.from_carrier(activity.to_carrier()).new_child()
    emitter.emit(activity, "fixture.activity")
    emitter.emit(adapter, "fixture.adapter")
    assert provider.force_flush()
    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    assert all(span.context.trace_id == int(root.trace_id, 16) for span in spans)
    assert all(set(span.attributes) <= {"salience.trace_id", "salience.span_id", "salience.parent_span_id"} for span in spans)
    provider.shutdown()


def test_active_span_identity_and_parent_are_exported_exactly():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    emitter = OpenTelemetryTraceEmitter(provider.get_tracer("p0-active"))
    root = TraceContext.new_root()
    with emitter.active_span(root, "api") as api_context:
        with emitter.active_span(TraceContext.from_carrier(api_context.to_carrier()), "activity") as activity_context:
            with emitter.active_span(TraceContext.from_carrier(activity_context.to_carrier()), "adapter"):
                pass
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["activity"].parent.span_id == spans["api"].context.span_id
    assert spans["adapter"].parent.span_id == spans["activity"].context.span_id
    for span in spans.values():
        assert span.attributes["salience.span_id"] == f"{span.context.span_id:016x}"
        assert span.attributes["salience.trace_id"] == f"{span.context.trace_id:032x}"
    provider.shutdown()


def test_exception_message_is_not_automatically_exported():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    emitter = OpenTelemetryTraceEmitter(provider.get_tracer("p0-errors"))
    with pytest.raises(RuntimeError):
        with emitter.active_span(TraceContext.new_root(), "fixture.failure"):
            raise RuntimeError("secret-canary-from-provider")
    assert "secret-canary-from-provider" not in exporter.get_finished_spans()[0].to_json()
    provider.shutdown()
