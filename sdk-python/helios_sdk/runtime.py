"""HELIOS runtime initialization.

``helios.init()`` wires an OpenTelemetry ``TracerProvider`` with two processors:

1. A ``BatchSpanProcessor`` exporting OTLP/HTTP to the collector (→ Tempo), so
   every agent run produces a real distributed trace.
2. The :class:`~helios_sdk.processor.HeliosSpanProcessor`, which writes the four
   structured records to ClickHouse.

Either side can be disabled (e.g. ``enable_otlp=False`` for offline unit tests).
The design is privacy-first: nothing leaves the local stack, and no external LLM
endpoint is contacted by the SDK itself.
"""
from __future__ import annotations

import atexit

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from . import semconv as S
from .clickhouse import ClickHouseWriter
from .processor import HeliosSpanProcessor

_TRACER_NAME = "helios.sdk"
_provider: TracerProvider | None = None
_helios_processor: HeliosSpanProcessor | None = None


def init(
    *,
    service_name: str = "helios-agent",
    otlp_endpoint: str = "http://localhost:4318",
    clickhouse_url: str = "http://localhost:8123",
    tenant_id: str = S.DEFAULT_TENANT,
    enable_otlp: bool = True,
    enable_clickhouse: bool = True,
) -> trace.Tracer:
    """Initialize HELIOS and return a tracer. Idempotent within a process."""
    global _provider, _helios_processor

    if _provider is not None:
        return trace.get_tracer(_TRACER_NAME)

    resource = Resource.create(
        {
            "service.name": service_name,
            S.SCHEMA_VERSION_KEY: S.SCHEMA_VERSION,
            S.TENANT_ID: tenant_id,
        }
    )
    provider = TracerProvider(resource=resource)

    if enable_otlp:
        # Imported lazily so offline use does not require the exporter extra.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces"))
        )

    if enable_clickhouse:
        _helios_processor = HeliosSpanProcessor(ClickHouseWriter(url=clickhouse_url))
        provider.add_span_processor(_helios_processor)

    trace.set_tracer_provider(provider)
    _provider = provider
    atexit.register(shutdown)
    return trace.get_tracer(_TRACER_NAME)


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


def flush() -> None:
    """Force both processors to flush. Useful in short-lived scripts."""
    if _provider is not None:
        _provider.force_flush()


def shutdown() -> None:
    global _provider, _helios_processor
    if _provider is not None:
        _provider.shutdown()
        _provider = None
        _helios_processor = None
