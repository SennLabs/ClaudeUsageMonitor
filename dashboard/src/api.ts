export interface Summary {
  total_sessions: number
  total_input_tokens: number
  total_output_tokens: number
  total_cache_read_tokens: number
  total_cache_creation_tokens: number
  total_cost_usd: number
  active_sessions: number
}

export interface SessionRow {
  session_id: string
  user_id: string | null
  organization_id: string | null
  project_name: string | null
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
