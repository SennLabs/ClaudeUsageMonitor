import type { Metric, TimeWindow } from './api'

export type { Metric, TimeWindow }

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
