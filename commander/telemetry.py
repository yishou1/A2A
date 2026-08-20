from __future__ import annotations

import functools
import os
import threading
from contextlib import contextmanager


_configured = False
_configure_lock = threading.Lock()


def configure_telemetry(service_name: str = "a2a-commander") -> None:
    global _configured
    with _configure_lock:
        if _configured:
            return
        _configured = True
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(
                resource=Resource.create({"service.name": service_name})
            )
            endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            if endpoint:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
                )
            trace.set_tracer_provider(provider)
        except Exception:
            return


def get_tracer():
    configure_telemetry()
    try:
        from opentelemetry import trace

        return trace.get_tracer("a2a.runtime")
    except Exception:
        return None


@contextmanager
def trace_span(name: str, attributes: dict | None = None):
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name, attributes=attributes or {}) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error.type", type(exc).__name__)
            raise


def traced_method(name: str, attribute_builder=None):
    def decorator(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            attributes = attribute_builder(*args, **kwargs) if attribute_builder else {}
            with trace_span(name, attributes):
                return function(*args, **kwargs)

        return wrapped

    return decorator
