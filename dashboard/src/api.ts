export interface AttributionRow {
  name: string
  requests: number
  cost_usd: number
  total_tokens: number
}

export interface Attribution {
  by_query_source: AttributionRow[]
  by_agent: AttributionRow[]
  by_skill: AttributionRow[]
  by_mcp_server: AttributionRow[]
  by_effort: AttributionRow[]
  by_speed: AttributionRow[]
}

export interface Latency {
  requests: number
  avg_ms: number | null
  p50_ms: number | null
  p95_ms: number | null
  max_ms: number | null
}

export interface ErrorStats {
  requests: number
  errors: number
  refusals: number
  retried: number
  error_rate: number
  by_status_code: { status_code: number; n: number }[]
  by_refusal_category: { category: string; n: number }[]
}

export interface ToolStat {
  tool_name: string
  calls: number
  failures: number
  avg_ms: number | null
  max_ms: number | null
}

export interface FleetRow {
  app_version: string
  terminal_type: string
  sessions: number
  last_seen_at: string
}

export const fetchAttribution = (hours = 24) =>
  getJSON<Attribution>(`/attribution?hours=${hours}`)
export const fetchLatency = (hours = 24) => getJSON<Latency>(`/latency?hours=${hours}`)
export const fetchErrorStats = (hours = 24) => getJSON<ErrorStats>(`/errors?hours=${hours}`)
export const fetchToolStats = (hours = 24) => getJSON<ToolStat[]>(`/tools?hours=${hours}`)
export const fetchFleet = () => getJSON<FleetRow[]>('/fleet')

export interface Summary {
  total_sessions: number
  total_input_tokens: number
  total_output_tokens: number
  total_cache_read_tokens: number
  total_cache_creation_tokens: number
  total_cost_usd: number
  active_sessions: number
  /** Events that arrived with no session.id — counted here but in no other view. */
  unattributed_events: number
  unattributed_cost_usd: number
}

export interface SessionRow {
  session_id: string
  user_id: string | null
  organization_id: string | null
  project_name: string | null
  /** How project_name was set. 'resource' = the container declared it. */
  project_source: 'resource' | 'user_map' | 'manual' | null
  first_seen_at: string
  last_seen_at: string
  event_count: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
  models: string | null
}

export interface ModelUsage {
  model: string
  requests: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

export interface UsageTimePoint {
  bucket: string
  cost_usd: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface ProjectTimePoint {
  bucket: string
  project_name: string  // "(untagged)" when no project is set
  cost_usd: number
  total_tokens: number
}

const BASE = '/api'

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) {
    throw new Error(`${path} failed with HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export const fetchSummary = () => getJSON<Summary>('/summary')
export const fetchSessions = () => getJSON<SessionRow[]>('/sessions')
export const fetchUsageByModel = () => getJSON<ModelUsage[]>('/usage-by-model')
// hours = 0 means all time; anything else is a look-back capped at 720h.
export const fetchUsageOverTime = (hours = 24) =>
  getJSON<UsageTimePoint[]>(`/usage-over-time?hours=${hours}`)

export const fetchUsageOverTimeByProject = (hours = 24) =>
  getJSON<ProjectTimePoint[]>(`/usage-over-time-by-project?hours=${hours}`)

export async function updateSessionProject(
  sessionId: string,
  projectName: string | null,
): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_name: projectName }),
  })
  if (!res.ok) throw new Error(`PATCH session failed with HTTP ${res.status}`)
}

// ── Users and user -> project mappings ─────────────────────────────────────

export interface UserRow {
  user_id: string
  project_name: string | null   // the standing mapping, not a per-session label
  organization_id: string | null
  session_count: number
  active_sessions: number
  first_seen_at: string | null
  last_seen_at: string | null
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

export const fetchUsers = () => getJSON<UserRow[]>('/users')
export const fetchProjects = () => getJSON<string[]>('/projects')

export interface UserProjectResult {
  ok: boolean
  project_name: string | null
  sessions_updated: number
}

/** Link a user id to a project. An empty name unlinks it. */
export async function setUserProject(
  userId: string,
  projectName: string | null,
): Promise<UserProjectResult> {
  const res = await fetch(`${BASE}/user-projects/${encodeURIComponent(userId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_name: projectName }),
  })
  if (!res.ok) throw new Error(`PUT user-project failed with HTTP ${res.status}`)
  return res.json() as Promise<UserProjectResult>
}

// ── Settings ───────────────────────────────────────────────────────────────

export type TimeWindow = '24h' | '7d' | '30d' | 'all'
export type Metric = 'cost' | 'tokens'

export interface AppSettings {
  monthlyBudget: number | null
  billingCycleDay: number
  refreshIntervalMs: number
  defaultTimeWindow: TimeWindow
  defaultMetric: Metric
  costAlertThresholdPerHour: number | null
  /** Read-only: set by the operator via ACTIVE_WINDOW_MINUTES. */
  activeSessionWindowMin: number
}

export const fetchSettings = () => getJSON<AppSettings>('/settings')

/** Merge a partial update. Returns the full settings as stored. */
export async function saveSettings(patch: Partial<AppSettings>): Promise<AppSettings> {
  const res = await fetch(`${BASE}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<AppSettings>
}

export interface BudgetUsage {
  cycle_start: string
  cost_usd: number
  total_tokens: number
}

/** Spend since the start of the current billing period. */
export const fetchBudget = () => getJSON<BudgetUsage>('/budget')

export interface BackupStatus {
  enabled: boolean
  destination: string | null
  method: 'rsync-ssh' | 'copy'
  interval_hours: number
  keep: number
  last_backup_at: string | null
  last_backup_ok: boolean | null
  last_backup_error: string | null
  next_backup_at: string | null
}

export const fetchBackupStatus = () => getJSON<BackupStatus>('/backup/status')

export async function triggerBackup(): Promise<BackupStatus> {
  const res = await fetch(`${BASE}/backup/trigger`, { method: 'POST' })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<BackupStatus>
}
