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
