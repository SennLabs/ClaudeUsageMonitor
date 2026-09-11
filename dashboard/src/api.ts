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

export interface SpendRate {
  window_minutes: number
  cost_usd: number
  cost_usd_per_hour: number
  events: number
}

/** Spend over a true trailing window, computed server-side from raw timestamps. */
export const fetchSpendRate = () => getJSON<SpendRate>('/rate')

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

export interface CacheRow {
  name?: string
  uncached_input_tokens: number
  cache_read_tokens: number
  cache_creation_tokens: number
  cost_usd: number
  hit_ratio: number | null
}

export interface CacheEfficiency {
  overall: CacheRow
  by_project: CacheRow[]
}

export interface PromptRow {
  prompt_id: string
  project_name: string
  session_id: string | null
  requests: number
  cost_usd: number
  total_tokens: number
  duration_ms: number
  started_at: string
  models: string | null
}

export interface Audit {
  permission_changes: {
    occurred_at: string
    session_id: string | null
    from_mode: string | null
    to_mode: string | null
    trigger: string | null
  }[]
  bypass_count: number
  auth_failures: {
    occurred_at: string
    session_id: string | null
    action: string | null
    success: string | null
    error_category: string | null
  }[]
  mcp_connections: {
    server: string
    status: string | null
    transport: string | null
    n: number
    last_at: string
  }[]
}

export const fetchCacheEfficiency = (hours = 24) =>
  getJSON<CacheEfficiency>(`/cache-efficiency?hours=${hours}`)
export const fetchPrompts = (hours = 24) => getJSON<PromptRow[]>(`/prompts?hours=${hours}`)
export const fetchAudit = (hours = 168) => getJSON<Audit>(`/audit?hours=${hours}`)

/** Not fetched — handed to the browser as a download. */
export const exportCsvUrl = (since?: string) =>
  `${BASE}/export.csv${since ? `?since=${encodeURIComponent(since)}` : ''}`

export interface FleetRow {
  app_version: string
  terminal_type: string
  sessions: number
  last_seen_at: string
}

/** One row of a metric grouped by an attribute (start type, active-time type, ...). */
export interface MetricGroup {
  name: string
  total: number
}

export interface EditDecision {
  language: string
  accepted: number
  rejected: number
  /** null when nothing was decided in this language — distinct from 0%. */
  acceptance_rate: number | null
}

/**
 * Claude Code's pre-aggregated metrics stream, and the ratios derived from it.
 * Every `derived` figure is null rather than 0 when its denominator is zero:
 * "no commits recorded" and "$0.00 per commit" are different statements.
 */
export interface ProductivityMetrics {
  window_hours: number | null
  /** false means nothing has ever arrived on /v1/metrics. */
  reporting: boolean
  /** Running totals, excluded from every sum. Fixable with a client setting. */
  cumulative_points_ignored: number
  /** Gauges and the like — not summable, and no client setting changes that. */
  unsummable_points: number
  totals: {
    lines_added: number
    lines_removed: number
    commits: number
    pull_requests: number
    active_seconds: number
    sessions_started: number
  }
  /** Restricted to sessions that also report metrics — matches `derived`. */
  cost_usd: number
  /** Every session in the window, the figure /api/summary agrees with. */
  cost_usd_fleet: number
  /** cost_usd / cost_usd_fleet. Below 1, `derived` describes a subset. */
  metrics_cost_coverage: number | null
  /** The metrics stream's own cost counter, for cross-checking `cost_usd`. */
  cost_usd_from_metrics: number
  derived: {
    cost_per_commit: number | null
    cost_per_pull_request: number | null
    cost_per_active_hour: number | null
    usd_per_1k_lines: number | null
    lines_per_active_hour: number | null
  }
  sessions_by_start_type: MetricGroup[]
  active_time_by_type: MetricGroup[]
  tokens_by_type: MetricGroup[]
  edit_decisions: EditDecision[]
  by_metric: { metric_name: string; points: number; total: number }[]
}

export const fetchProductivityMetrics = (hours = 168) =>
  getJSON<ProductivityMetrics>(`/metrics?hours=${hours}`)

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
export interface SessionPage {
  total: number
  limit: number
  offset: number
  sessions: SessionRow[]
}

/** `total` lets the UI say when the list is truncated rather than quietly disagreeing with the summary. */
export const fetchSessions = (limit = 100, offset = 0) =>
  getJSON<SessionPage>(`/sessions?limit=${limit}&offset=${offset}`)
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
  /** IANA zone name. Buckets days to local midnight rather than UTC midnight. */
  displayTimeZone: string
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
