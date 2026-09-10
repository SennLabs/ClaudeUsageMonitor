export interface ModelPrice {
  input_per_mtok: number   // $ per 1 million input tokens
  output_per_mtok: number  // $ per 1 million output tokens
}

export type TimeWindow = '24h' | '7d' | '30d' | 'all'

/** Hours to request per window. 0 is the API's "all time" sentinel. */
export const WINDOW_HOURS: Record<TimeWindow, number> = {
  '24h': 24,
  '7d': 168,
  '30d': 720,
  all: 0,
}

export const WINDOW_OPTIONS: { value: TimeWindow; label: string }[] = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: 'all', label: 'All' },
]

export type Metric = 'cost' | 'tokens'

export interface AppSettings {
  monthlyBudget: number | null
  billingCycleDay: number         // 1–28, day of month billing resets
  refreshIntervalMs: number       // poll interval for live data
  activeSessionWindowMin: number  // minutes until a session is "inactive"
  defaultTimeWindow: TimeWindow   // chart time window on first load
  defaultMetric: Metric           // chart metric on first load
  costAlertThresholdPerHour: number | null  // show warning when hourly rate exceeds this
  modelPrices: Record<string, ModelPrice>
}

export const DEFAULT_SETTINGS: AppSettings = {
  monthlyBudget: null,
  billingCycleDay: 1,
  refreshIntervalMs: 5000,
  activeSessionWindowMin: 15,
  defaultTimeWindow: '24h',
  defaultMetric: 'cost',
  costAlertThresholdPerHour: null,
  modelPrices: {},
}

const STORAGE_KEY = 'claudeMonitorSettings'

export function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULT_SETTINGS, modelPrices: {} }
    const parsed = JSON.parse(raw) as Partial<AppSettings>
    return { ...DEFAULT_SETTINGS, ...parsed, modelPrices: parsed.modelPrices ?? {} }
  } catch {
    return { ...DEFAULT_SETTINGS, modelPrices: {} }
  }
}

export function saveSettings(s: AppSettings): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(s))
}

/**
 * Estimate current hourly spend from time-series data.
 * For hourly buckets (0 < hours ≤ 48): sum the last hour's worth of points.
 * For daily buckets (hours > 48, or all-time): take the newest bucket / 24.
 */
export function computeHourlyRate(
  pts: { bucket: string; cost_usd: number }[],
  hours: number,
): number {
  if (!pts || pts.length === 0) return 0
  // hours = 0 is all-time, which comes back in daily buckets like the long windows.
  if (hours > 0 && hours <= 48) {
    const cutoff = Date.now() - 3_600_000
    return pts
      .filter((p) => new Date(p.bucket).getTime() >= cutoff)
      .reduce((a, p) => a + p.cost_usd, 0)
  }
  const sorted = [...pts].sort((a, b) => b.bucket.localeCompare(a.bucket))
  return sorted[0].cost_usd / 24
}

/**
 * If the user has set a price override for this model, recalculate from tokens.
 * Otherwise return the cost reported by Claude Code verbatim.
 */
export function adjustedCost(
  model: string | null | undefined,
  inputTokens: number,
  outputTokens: number,
  reportedCost: number,
  prices: Record<string, ModelPrice>,
): number {
  if (!model) return reportedCost
  const p = prices[model]
  if (!p) return reportedCost
  return (inputTokens * p.input_per_mtok + outputTokens * p.output_per_mtok) / 1_000_000
}
