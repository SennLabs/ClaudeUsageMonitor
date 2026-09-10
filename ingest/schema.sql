CREATE TABLE IF NOT EXISTS sessions (
    session_id       TEXT PRIMARY KEY,
    user_id          TEXT,
    organization_id  TEXT,
    first_seen_at    TEXT,
    last_seen_at     TEXT,
    end_reason       TEXT,
    project_name     TEXT
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
    raw_attributes         TEXT
);

-- Standing user.id -> project mapping. Dev containers report a stable user.id,
-- so mapping it once labels every session that container ever opens.
CREATE TABLE IF NOT EXISTS user_projects (
    user_id       TEXT PRIMARY KEY,
    project_name  TEXT NOT NULL,
    updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_usage_events_session ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_events_time ON usage_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
