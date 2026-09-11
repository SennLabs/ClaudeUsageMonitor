CREATE TABLE IF NOT EXISTS sessions (
    session_id       TEXT PRIMARY KEY,
    user_id          TEXT,
    organization_id  TEXT,
    first_seen_at    TEXT,
    last_seen_at     TEXT,
    end_reason       TEXT,
    project_name     TEXT,
    -- How project_name was set, so precedence is inspectable rather than
    -- implied: 'resource' (the container declared it), 'user_map' (a user.id
    -- mapping filled it in), 'manual' (someone tagged this one session).
    project_source   TEXT
);

CREATE TABLE IF NOT EXISTS usage_events (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id             TEXT REFERENCES sessions(session_id),
    occurred_at            TEXT NOT NULL,
    event_name             TEXT,
    model                  TEXT,
    input_tokens           INTEGER,
    output_tokens          INTEGER,
    cache_read_tokens      INTEGER,
    cache_creation_tokens  INTEGER,
    cost_usd               REAL,
    cost_usd_micros        INTEGER,
    -- Attributes Claude Code has always sent. They were captured in
    -- raw_attributes but unusable in aggregate queries; see docs/data-model.md.
    duration_ms            INTEGER,
    query_source           TEXT,     -- main | subagent | auxiliary
    effort                 TEXT,     -- low | medium | high | xhigh | max
    speed                  TEXT,     -- fast | normal
    agent_name             TEXT,
    skill_name             TEXT,
    mcp_server_name        TEXT,
    prompt_id              TEXT,
    app_version            TEXT,
    terminal_type          TEXT,
    tool_name              TEXT,
    status_code            INTEGER,
    raw_attributes         TEXT,
    -- Stable digest of the record's identity. An OTLP exporter retries a 5xx
    -- by resending the identical batch, so without this a transient failure
    -- permanently double-counts cost and tokens. The UNIQUE index that backs
    -- INSERT OR IGNORE is created from init_db(), not here — an existing
    -- database may already contain duplicates, and failing startup over that
    -- would be worse than running without dedupe.
    event_hash             TEXT
);

-- Standing user.id -> project mapping. Dev containers report a stable user.id,
-- so mapping it once labels every session that container ever opens.
CREATE TABLE IF NOT EXISTS user_projects (
    user_id       TEXT PRIMARY KEY,
    project_name  TEXT NOT NULL,
    updated_at    TEXT
);

-- Dashboard preferences. Single row: these are instance-wide, not per-viewer.
-- Previously they lived in each browser's localStorage, which meant the wall
-- tablet and a laptop disagreed and the server could not act on any of them.
CREATE TABLE IF NOT EXISTS app_settings (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    data  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_events_session ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_events_time ON usage_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
-- Every non-cost view filters by event type first.
CREATE INDEX IF NOT EXISTS idx_usage_events_name ON usage_events(event_name);

-- Claude Code's pre-aggregated metrics stream (OTEL_METRICS_EXPORTER=otlp),
-- received on POST /v1/metrics. Kept in its own table rather than folded into
-- usage_events: these are periodic aggregates over a time window, not records
-- of a single API call, and they arrive on a different interval (60s vs 5s).
CREATE TABLE IF NOT EXISTS metric_points (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name     TEXT NOT NULL,
    occurred_at     TEXT NOT NULL,   -- end of the point's window
    started_at      TEXT,            -- start of the point's window
    value           REAL NOT NULL,
    -- delta | cumulative | unspecified. Claude Code exports delta, where each
    -- point is an increment and SUM() is correct. A cumulative point is a
    -- running total, so summing them counts the same work once per export
    -- interval — every read here filters to delta and reports the rest.
    temporality     TEXT,
    is_monotonic    INTEGER,
    session_id      TEXT,
    user_id         TEXT,
    organization_id TEXT,
    project_name    TEXT,
    app_version     TEXT,
    terminal_type   TEXT,
    model           TEXT,
    type            TEXT,            -- added|removed, user|cli, input|output|...
    tool_name       TEXT,
    decision        TEXT,            -- accept | reject
    source          TEXT,
    language        TEXT,
    start_type      TEXT,            -- fresh | resume | continue
    raw_attributes  TEXT,
    -- Same reasoning as usage_events.event_hash: the exporter resends an
    -- identical payload after a 5xx, and a re-counted delta is a permanently
    -- wrong total. The UNIQUE index is created from init_db().
    point_hash      TEXT
);

CREATE INDEX IF NOT EXISTS idx_metric_points_name_time ON metric_points(metric_name, occurred_at);
CREATE INDEX IF NOT EXISTS idx_metric_points_session ON metric_points(session_id);
