"""Parser for OTLP/HTTP JSON-encoded ExportLogsServiceRequest payloads.

Claude Code (with OTEL_LOGS_EXPORTER=otlp, OTEL_EXPORTER_OTLP_PROTOCOL=http/json)
POSTs this shape to /v1/logs. It's the standard OTLP JSON encoding, not
Claude-Code-specific - see https://opentelemetry.io/docs/specs/otlp/.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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
}

_INT_FIELDS = {"input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"}


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
    raw_attributes: dict[str, Any] = field(default_factory=dict)


def _decode_value(value: dict[str, Any] | None) -> Any:
    """Decode one OTLP AnyValue JSON object into a plain Python value."""
    if not value:
        return None
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        # 64-bit ints are encoded as strings in OTLP JSON to avoid precision loss.
        return int(value["intValue"])
    if "doubleValue" in value:
        return value["doubleValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "arrayValue" in value:
        return [_decode_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return _decode_attributes(value["kvlistValue"].get("values", []))
    return None


def _decode_attributes(attributes: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for attr in attributes:
        key = attr.get("key")
        if key is None:
            continue
        result[key] = _decode_value(attr.get("value"))
    return result


def _nanos_to_iso(time_unix_nano: str | int | None) -> str:
    if not time_unix_nano:
        return datetime.now(timezone.utc).isoformat()
    seconds = int(time_unix_nano) / 1_000_000_000
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def extract_log_events(body: dict[str, Any]) -> list[LogEvent]:
    """Flatten an OTLP ExportLogsServiceRequest (JSON-encoded) into LogEvent rows."""
    events: list[LogEvent] = []

    for resource_logs in body.get("resourceLogs", []):
        resource_attrs = _decode_attributes(resource_logs.get("resource", {}).get("attributes", []))

        for scope_logs in resource_logs.get("scopeLogs", []):
            for record in scope_logs.get("logRecords", []):
                record_attrs = _decode_attributes(record.get("attributes", []))
                merged = {**resource_attrs, **record_attrs}

                event_name = _decode_value(record.get("body"))
                if isinstance(event_name, (dict, list)):
                    event_name = None

                fields: dict[str, Any] = {}
                for raw_key, raw_value in merged.items():
                    normalized = _ATTR_ALIASES.get(raw_key)
                    if not normalized:
                        continue
                    value = raw_value
                    if normalized in _INT_FIELDS and value is not None:
                        try:
                            value = int(value)
                        except (TypeError, ValueError):
                            continue
                    fields[normalized] = value

                events.append(
                    LogEvent(
                        occurred_at=_nanos_to_iso(record.get("timeUnixNano")),
                        event_name=event_name,
                        raw_attributes=merged,
                        **fields,
                    )
                )

    return events
