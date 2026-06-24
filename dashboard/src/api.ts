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
