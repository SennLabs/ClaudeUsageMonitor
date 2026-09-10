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
