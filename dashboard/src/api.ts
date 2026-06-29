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
