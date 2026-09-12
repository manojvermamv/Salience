from contextlib import contextmanager
from dataclasses import dataclass
from secrets import token_hex
from typing import Iterator

from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    TraceState,
    set_span_in_context,
)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: str | None = None

    @classmethod
    def new_root(cls) -> "TraceContext":
        return cls(trace_id=token_hex(16), span_id=token_hex(8))

    def new_child(self) -> "TraceContext":
        return TraceContext(
            trace_id=self.trace_id,
            span_id=token_hex(8),
            parent_span_id=self.span_id,
        )

    def to_carrier(self) -> dict[str, str]:
        carrier = {"traceparent": f"00-{self.trace_id}-{self.span_id}-01"}
        if self.parent_span_id is not None:
            carrier["x-salience-parent-span-id"] = self.parent_span_id
        return carrier

    @classmethod
    def from_carrier(cls, carrier: dict[str, str]) -> "TraceContext":
        version, trace_id, span_id, flags = carrier["traceparent"].split("-")
        if version != "00" or flags not in {"00", "01"}:
            raise ValueError("unsupported traceparent")
        if len(trace_id) != 32 or len(span_id) != 16:
            raise ValueError("invalid traceparent identifiers")
        int(trace_id, 16)
        int(span_id, 16)
        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=carrier.get("x-salience-parent-span-id"),
        )

    def otel_parent_context(self):
        span_context = SpanContext(
            trace_id=int(self.trace_id, 16),
            span_id=int(self.span_id, 16),
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        )
        return set_span_in_context(NonRecordingSpan(span_context))


class OpenTelemetryTraceEmitter:
    def __init__(self, tracer: trace.Tracer) -> None:
        self._tracer = tracer

    @contextmanager
    def span(self, context: TraceContext, name: str) -> Iterator[None]:
        with self._tracer.start_as_current_span(
            name,
            context=context.otel_parent_context(),
        ) as span:
            span.set_attribute("salience.trace_id", context.trace_id)
            span.set_attribute("salience.span_id", context.span_id)
            if context.parent_span_id is not None:
                span.set_attribute("salience.parent_span_id", context.parent_span_id)
            yield

    def emit(self, context: TraceContext, name: str) -> None:
        with self.span(context, name):
            pass
