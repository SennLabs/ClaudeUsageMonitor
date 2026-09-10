"""Parser for OTLP/HTTP JSON-encoded ExportLogsServiceRequest payloads.

Claude Code (with OTEL_LOGS_EXPORTER=otlp, OTEL_EXPORTER_OTLP_PROTOCOL=http/json)
POSTs this shape to /v1/logs. It's the standard OTLP JSON encoding, not
Claude-Code-specific - see https://opentelemetry.io/docs/specs/otlp/.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Known event attribute keys -> normalized field name. A couple of aliases
# are included since exporter attribute naming has shifted across versions;
# anything not listed here still lands in raw_attributes.
_ATTR_ALIASES = {
    "session.id": "session_id",
    "session_id": "session_id",
    "user.id": "user_id",
    "organization.id": "organization_id",
    "model": "model",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_tokens",
    "cache_read_input_tokens": "cache_read_tokens",
    "cache_creation_tokens": "cache_creation_tokens",
    "cache_creation_input_tokens": "cache_creation_tokens",
    "cost_usd": "cost_usd",
    "cost_usd_micros": "cost_usd_micros",
    # Set per container via OTEL_RESOURCE_ATTRIBUTES=project=<name>. Claude Code
    # attaches custom resource attributes to every event, which makes the
    # container itself declare its project — the durable alternative to mapping
    # user.id, which is a per-installation ID and changes on every rebuild.
    "project": "project_name",
    "project.name": "project_name",
    "project_name": "project_name",
    # ── Already arriving, previously only ever stored in raw_attributes ──
    "duration_ms": "duration_ms",
    "query_source": "query_source",   # main | subagent | auxiliary
    "effort": "effort",               # low | medium | high | xhigh | max
    "speed": "speed",                 # fast | normal
    "agent.name": "agent_name",
    "skill.name": "skill_name",
    "mcp_server.name": "mcp_server_name",
    "prompt.id": "prompt_id",
    "app.version": "app_version",
    "terminal.type": "terminal_type",
    "tool_name": "tool_name",
    "status_code": "status_code",
}

_INT_FIELDS = {
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "cost_usd_micros",
    "duration_ms",
    "status_code",
}

_STR_FIELDS = {
    "session_id",
    "user_id",
    "organization_id",
    "model",
    "project_name",
    "query_source",
    "effort",
    "speed",
    "agent_name",
    "skill_name",
    "mcp_server_name",
    "prompt_id",
    "app_version",
    "terminal_type",
    "tool_name",
}

# Anything SQLite can bind directly. A nested kvlistValue/arrayValue decodes to
# a dict or list, which must not reach the insert — it raises at bind time and
# takes the whole batch with it.
_SCALARS = (str, int, float, bool)


@dataclass
class LogEvent:
    occurred_at: str
    event_name: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    organization_id: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cost_usd: float | None = None
    # Integer millionths, as reported by Claude Code. Exact where cost_usd is a
    # float that accumulates error across SUM() over a growing table.
    cost_usd_micros: int | None = None
    # From the container's own resource attributes, not from any mapping table.
    project_name: str | None = None
    duration_ms: int | None = None
    query_source: str | None = None
    effort: str | None = None
    speed: str | None = None
    agent_name: str | None = None
    skill_name: str | None = None
    mcp_server_name: str | None = None
    prompt_id: str | None = None
    app_version: str | None = None
    terminal_type: str | None = None
    tool_name: str | None = None
    status_code: int | None = None
    raw_attributes: dict[str, Any] = field(default_factory=dict)


def _decode_value(value: dict[str, Any] | None) -> Any:
    """Decode one OTLP AnyValue JSON object into a plain Python value."""
    if not value:
        return None
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        # 64-bit ints are encoded as strings in OTLP JSON to avoid precision loss.
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return None  # malformed value: drop it rather than 500 the batch
    if "doubleValue" in value:
        return value["doubleValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "arrayValue" in value:
        return [_decode_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return _decode_attributes(value["kvlistValue"].get("values", []))
    return None


def _decode_attributes(attributes: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(attributes, list):
        return result  # null or wrong shape — treat as "no attributes"
    for attr in attributes:
        if not isinstance(attr, dict):
            continue
        key = attr.get("key")
        if key is None:
            continue
        result[key] = _decode_value(attr.get("value"))
    return result


def _nanos_to_iso(time_unix_nano: str | int | None) -> str:
    """
    Convert an OTLP nanosecond timestamp to a UTC ISO 8601 string.

    A missing, zero, or unparseable value falls back to now. Note "0" as a
    *string* is truthy, so it is checked numerically rather than for falsiness —
    otherwise such an event lands in 1970 and disappears from every windowed
    chart while pinning its session's first_seen_at.
    """
    try:
        nanos = int(time_unix_nano) if time_unix_nano is not None else 0
    except (TypeError, ValueError):
        nanos = 0
    if nanos <= 0:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(nanos / 1_000_000_000, tz=timezone.utc).isoformat()


def _iter_dicts(container: Any, key: str) -> list[dict[str, Any]]:
    """Return container[key] if it is a list of dicts, else an empty list."""
    value = container.get(key) if isinstance(container, dict) else None
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def extract_log_events(body: Any) -> tuple[list[LogEvent], int]:
    """
    Flatten an OTLP ExportLogsServiceRequest (JSON-encoded) into LogEvent rows.

    Returns the events plus a count of records that were skipped for being
    structurally unusable. Malformed *records* are dropped individually rather
    than failing the batch — an exporter retries a 5xx, and with no idempotency
    key that permanently double-counts everything that did parse. A malformed
    *envelope* raises ValueError, which the route turns into a 400.
    """
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    if "resourceLogs" not in body:
        raise ValueError("body has no resourceLogs")
    if not isinstance(body["resourceLogs"], list):
        raise ValueError("resourceLogs must be an array")

    events: list[LogEvent] = []
    skipped = 0

    for resource_logs in _iter_dicts(body, "resourceLogs"):
        resource = resource_logs.get("resource")
        resource_attrs = _decode_attributes(
            resource.get("attributes") if isinstance(resource, dict) else None
        )

        for scope_logs in _iter_dicts(resource_logs, "scopeLogs"):
            for record in _iter_dicts(scope_logs, "logRecords"):
                try:
                    events.append(_build_event(record, resource_attrs))
                except Exception as exc:  # one bad record must not lose the batch
                    skipped += 1
                    log.warning("Skipped an unparseable log record: %s", exc)

    return events, skipped


def _build_event(record: dict[str, Any], resource_attrs: dict[str, Any]) -> LogEvent:
    record_attrs = _decode_attributes(record.get("attributes"))
    merged = {**resource_attrs, **record_attrs}

    event_name = _decode_value(record.get("body"))
    if isinstance(event_name, (dict, list)):
        event_name = None

    fields: dict[str, Any] = {}
    for raw_key, raw_value in merged.items():
        normalized = _ATTR_ALIASES.get(raw_key)
        if not normalized or raw_value is None:
            continue
        value = raw_value
        if normalized in _INT_FIELDS:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        elif normalized == "cost_usd":
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue  # e.g. a locale-formatted "1,25" would otherwise store as text
        elif normalized in _STR_FIELDS:
            if not isinstance(value, _SCALARS):
                continue  # structured value where a name was expected
            value = str(value)
        fields[normalized] = value

    # Older clients send only the decimal figure; derive the exact integer form
    # so downstream aggregation has one column to trust.
    if "cost_usd_micros" not in fields and "cost_usd" in fields:
        fields["cost_usd_micros"] = round(fields["cost_usd"] * 1_000_000)

    return LogEvent(
        occurred_at=_nanos_to_iso(record.get("timeUnixNano")),
        event_name=event_name,
        raw_attributes=merged,
        **fields,
    )
