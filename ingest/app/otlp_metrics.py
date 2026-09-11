"""Parser for OTLP/HTTP JSON-encoded ExportMetricsServiceRequest payloads.

Claude Code (with OTEL_METRICS_EXPORTER=otlp) POSTs this to /v1/metrics. It is
a different shape from the logs stream, not a copy of it:

  * resourceMetrics -> scopeMetrics -> metrics -> {sum,gauge,histogram}.dataPoints
  * the default export interval is 60s, against 5s for logs
  * sums default to DELTA temporality, so each point is an increment, not a
    running total

The eight metrics Claude Code emits are listed in docs/data-model.md. Nothing
here is specific to them: an unrecognised metric is still stored with its name,
value and full attribute map, the same way the logs parser keeps unknown
attributes in raw_attributes. That is deliberate — attribute and metric naming
has shifted across Claude Code versions before.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from .otlp import _decode_attributes, _iter_dicts, _nanos_to_iso

log = logging.getLogger(__name__)

# OTLP AggregationTemporality enum. Sums arrive as DELTA from Claude Code;
# CUMULATIVE points are stored but excluded from every SUM(), because adding
# running totals together counts the same work once per export interval.
_TEMPORALITY = {0: "unspecified", 1: "delta", 2: "cumulative"}

# Metric data-point attributes promoted to columns, so they are usable in a
# GROUP BY rather than only via json_extract. Same convention as the logs
# parser's _ATTR_ALIASES.
_ATTR_ALIASES = {
    "session.id": "session_id",
    "session_id": "session_id",
    "user.id": "user_id",
    "organization.id": "organization_id",
    "model": "model",
    "project": "project_name",
    "project.name": "project_name",
    "project_name": "project_name",
    "app.version": "app_version",
    "terminal.type": "terminal_type",
    # lines_of_code.count: added | removed. active_time.total: user | cli.
    # token.usage: input | output | cacheRead | cacheCreation.
    "type": "type",
    "tool_name": "tool_name",
    "decision": "decision",        # accept | reject
    "source": "source",
    "language": "language",
    "start_type": "start_type",    # fresh | resume | continue
}

_STR_FIELDS = set(_ATTR_ALIASES.values())

_SCALARS = (str, int, float, bool)


@dataclass
class MetricPoint:
    metric_name: str
    occurred_at: str
    value: float
    started_at: str | None = None
    temporality: str = "unspecified"
    is_monotonic: bool = False
    session_id: str | None = None
    user_id: str | None = None
    organization_id: str | None = None
    project_name: str | None = None
    app_version: str | None = None
    terminal_type: str | None = None
    model: str | None = None
    type: str | None = None
    tool_name: str | None = None
    decision: str | None = None
    source: str | None = None
    language: str | None = None
    start_type: str | None = None
    raw_attributes: dict[str, Any] = field(default_factory=dict)


def _point_value(point: dict[str, Any], kind: str) -> float | None:
    """
    The numeric value of one data point.

    Number points carry exactly one of asDouble / asInt (an int is JSON-encoded
    as a string, as everywhere else in OTLP JSON). A histogram has no single
    value; its `sum` is the only figure that aggregates, so that is what is
    stored, and `count` rides along in raw_attributes.
    """
    if kind == "histogram":
        raw = point.get("sum")
    elif "asDouble" in point:
        raw = point.get("asDouble")
    elif "asInt" in point:
        raw = point.get("asInt")
    else:
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def extract_metric_points(body: Any) -> tuple[list[MetricPoint], int]:
    """
    Flatten an OTLP ExportMetricsServiceRequest (JSON-encoded) into rows.

    Returns the points plus a count of data points skipped for being
    structurally unusable. As with the logs path, one bad point is dropped
    individually rather than failing the batch: a 5xx makes the exporter resend
    the identical payload, which double-counts everything that did parse. A
    malformed *envelope* raises ValueError, which the route turns into a 400.
    """
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    if "resourceMetrics" not in body:
        raise ValueError("body has no resourceMetrics")
    if not isinstance(body["resourceMetrics"], list):
        raise ValueError("resourceMetrics must be an array")

    points: list[MetricPoint] = []
    skipped = 0

    for resource_metrics in _iter_dicts(body, "resourceMetrics"):
        resource = resource_metrics.get("resource")
        resource_attrs = _decode_attributes(
            resource.get("attributes") if isinstance(resource, dict) else None
        )

        for scope_metrics in _iter_dicts(resource_metrics, "scopeMetrics"):
            for metric in _iter_dicts(scope_metrics, "metrics"):
                name = metric.get("name")
                if not isinstance(name, str) or not name:
                    # Count the points lost, not the metric. One nameless
                    # metric carrying 300 data points is 300 dropped
                    # measurements, and the operator-facing log line is the
                    # only signal that anything was discarded at all.
                    skipped += _count_points(metric)
                    continue
                handled = False
                for kind in ("sum", "gauge", "histogram", "exponentialHistogram"):
                    data = metric.get(kind)
                    if not isinstance(data, dict):
                        continue
                    handled = True
                    normalized = "histogram" if "istogram" in kind else kind
                    temporality = _TEMPORALITY.get(
                        data.get("aggregationTemporality"), "unspecified"
                    )
                    monotonic = bool(data.get("isMonotonic"))
                    for point in _iter_dicts(data, "dataPoints"):
                        try:
                            built = _build_point(
                                name, point, normalized, temporality, monotonic, resource_attrs
                            )
                        except Exception as exc:  # one bad point must not lose the batch
                            skipped += 1
                            log.warning("Skipped an unparseable metric point: %s", exc)
                            continue
                        if built is None:
                            skipped += 1
                            continue
                        points.append(built)
                if not handled:
                    # An OTLP data kind this parser does not read — `summary`,
                    # or something added to the spec later. Silently returning
                    # zero made the drop invisible.
                    skipped += _count_points(metric)
                    log.warning(
                        "Metric %r uses an unsupported data kind (%s); its points were dropped",
                        name,
                        ", ".join(k for k in metric if k not in ("name", "unit", "description"))
                        or "none",
                    )

    return points, skipped


def _count_points(metric: dict[str, Any]) -> int:
    """How many data points a metric carries, whatever kind it is."""
    total = 0
    for value in metric.values():
        if isinstance(value, dict):
            total += len(_iter_dicts(value, "dataPoints"))
    return max(total, 1)  # a metric we cannot read at all still counts as one loss


def _build_point(
    metric_name: str,
    point: dict[str, Any],
    kind: str,
    temporality: str,
    monotonic: bool,
    resource_attrs: dict[str, Any],
) -> MetricPoint | None:
    value = _point_value(point, kind)
    if value is None:
        return None

    point_attrs = _decode_attributes(point.get("attributes"))
    merged = {**resource_attrs, **point_attrs}
    if kind == "histogram" and point.get("count") is not None:
        merged.setdefault("_histogram_count", point["count"])

    fields: dict[str, Any] = {}
    for raw_key, raw_value in merged.items():
        normalized = _ATTR_ALIASES.get(raw_key)
        if not normalized or raw_value is None:
            continue
        if normalized in _STR_FIELDS:
            if not isinstance(raw_value, _SCALARS):
                continue  # structured value where a name was expected
            fields[normalized] = str(raw_value)

    start = point.get("startTimeUnixNano")
    return MetricPoint(
        metric_name=metric_name,
        occurred_at=_nanos_to_iso(point.get("timeUnixNano")),
        started_at=_nanos_to_iso(start) if start else None,
        value=value,
        temporality=temporality,
        is_monotonic=monotonic,
        raw_attributes=merged,
        **fields,
    )
