"""OpenTelemetry tracing for tapps-brain hot paths (STORY-061.1).

Provides lightweight span instrumentation for remember, recall, search,
and hive operations.  The ``opentelemetry-api`` package (core dep) is
always available as a no-op when no SDK is configured; actual export
requires ``pip install tapps-brain[otel]``.

Span names are aligned with ``docs/engineering/system-architecture.md``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

try:
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, StatusCode

    _HAS_OTEL_API = True
except ImportError:  # pragma: no cover
    _HAS_OTEL_API = False

if TYPE_CHECKING:
    from collections.abc import Iterator

# ---------------------------------------------------------------------------
# Canonical span names — aligned with system-architecture.md
# ---------------------------------------------------------------------------

#: Span name for the ``remember`` (save) operation.
SPAN_REMEMBER: str = "tapps_brain.remember"

#: Span name for the ``recall`` (search + inject) operation.
SPAN_RECALL: str = "tapps_brain.recall"

#: Span name for the low-level ``search`` operation.
SPAN_SEARCH: str = "tapps_brain.search"

#: Span name for Hive propagation on save.
SPAN_HIVE_PROPAGATE: str = "tapps_brain.hive.propagate"

#: Span name for Hive search during group-aware recall.
SPAN_HIVE_SEARCH: str = "tapps_brain.hive.search"

# ---------------------------------------------------------------------------
# Instrumentation identity
# ---------------------------------------------------------------------------

_INSTRUMENTATION_NAME: str = "tapps_brain"


def _service_name() -> str:
    """Return ``service.name`` from env, defaulting to ``"tapps-brain"``."""
    return os.environ.get("OTEL_SERVICE_NAME", "tapps-brain")


def _service_version() -> str:
    """Return ``service.version`` from env, defaulting to ``""``."""
    return os.environ.get("OTEL_SERVICE_VERSION", "")


def get_tracer() -> Any:  # noqa: ANN401
    """Return the OTel Tracer for tapps-brain.

    When ``opentelemetry-api`` is not installed, returns ``None``.  When
    installed without the SDK, returns a no-op tracer (zero allocation on
    the hot path).

    The ``service.name`` / ``service.version`` resource attributes are set
    by the OTel SDK when configured via ``OTEL_SERVICE_NAME`` /
    ``OTEL_SERVICE_VERSION`` environment variables.
    """
    if not _HAS_OTEL_API:  # pragma: no cover
        return None
    return trace.get_tracer(_INSTRUMENTATION_NAME)


@contextmanager
def start_span(
    name: str,
    attributes: dict[str, str | int | float | bool] | None = None,
    *,
    record_exception: bool = True,
) -> Iterator[Any]:
    """Context manager that wraps an operation in an OTel INTERNAL span.

    No-op when ``opentelemetry-api`` is not available.  Exceptions are
    recorded on the span and re-raised; span status is set to ``ERROR`` on
    exception and ``OK`` on success.

    .. warning::
        **Never** pass raw memory content, entry keys, query strings, or
        user PII as attribute values.  See the telemetry policy doc.

    Args:
        name: Span name — use module-level ``SPAN_*`` constants.
        attributes: Safe span attributes (tier, scope, result counts, etc.).
            Must **not** contain memory content, entry keys, or query text.
        record_exception: When ``True``, caught exceptions are recorded on
            the span before being re-raised.

    Yields:
        The OTel :class:`opentelemetry.trace.Span` instance when OTel is
        available, or ``None`` when it is not.
    """
    if not _HAS_OTEL_API:  # pragma: no cover
        yield None
        return

    tracer = get_tracer()
    if tracer is None:  # pragma: no cover
        yield None
        return

    with tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        try:
            yield span
            span.set_status(StatusCode.OK)
        except Exception as exc:
            if record_exception:
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, str(exc))
            raise
