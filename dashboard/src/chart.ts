/**
 * Pure chart maths and label formatting, extracted from UsageChart so it can be
 * tested without mounting a component. Nothing here touches Solid or the DOM.
 */
import type { Metric } from './api'

// Bucket granularity mirrors the server's _granularity(): hourly up to 48h,
// daily beyond that and for all-time (hours = 0). Labelling has to follow it —
// formatting a daily bucket as a clock time renders every point identically.
export type Granularity = 'hour' | 'day'

export function granularityFor(hours: number | undefined): Granularity {
  if (hours === undefined) return 'hour'
  return hours > 0 && hours <= 48 ? 'hour' : 'day'
}

export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * Bucket labels are read straight off the string, never through `new Date`.
 *
 * The server already names each bucket in the configured display zone —
 * `2026-09-11T00:00:00Z` under the UTC default, `2026-09-11T00:00:00+08:00`
 * for Australia/Perth. Parsing that into a Date and formatting it re-applies
 * *the browser's* zone on top, which shifts the label off the bucket it
 * belongs to: a Perth day start viewed from a UTC browser renders as the
 * previous day at 16:00. The characters in the string are already the answer.
 */
export function parts(bucket: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2})/.exec(bucket)
  if (!m) return null
  return { year: +m[1], month: +m[2] - 1, day: +m[3], hour: m[4] }
}

export function fmtHour(bucket: string) {
  return `${parts(bucket)?.hour ?? '??'}:00`
}
export function fmtDay(bucket: string, withYear = false) {
  const p = parts(bucket)
  if (!p) return bucket
  const base = `${p.day} ${MONTHS[p.month]}`
  return withYear ? `${base} ${String(p.year).slice(2)}` : base
}
export function fmtDateTime(bucket: string) {
  const p = parts(bucket)
  if (!p) return bucket
  return `${p.day} ${MONTHS[p.month]} ${p.hour}:00`
}
export function xLabel(bucket: string, g: Granularity, multiYear: boolean) {
  return g === 'hour' ? fmtHour(bucket) : fmtDay(bucket, multiYear)
}
export function tooltipLabel(bucket: string, g: Granularity) {
  if (g === 'hour') return fmtDateTime(bucket)
  return `${fmtDay(bucket)} ${parts(bucket)?.year ?? ''}`.trim()
}
export function fmtY(v: number, metric: Metric, max: number) {
  if (metric === 'cost') {
    // Scale precision to the axis. niceMax can return sub-cent maxima, where
    // toFixed(2) rendered every tick as an identical "$0.00".
    const dp = max >= 1 ? 2 : max >= 0.1 ? 3 : 4
    return `$${v.toFixed(dp)}`
  }
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1).replace(/\.0$/, '')}k`
  // Token counts are integers; fractional ticks are meaningless.
  return String(Math.round(v))
}
export function niceMax(raw: number) {
  if (raw === 0) return 1
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const rounded = Math.ceil(raw / mag) * mag
  // Sub-unit maxima are always a cost axis — token counts are integers, so the
  // only one below 1 is 0, handled above. Leave them alone: the multiple-of-4
  // rule below is an *integer* rule, and applying it here pushed a $0.04 peak
  // onto a $4.00 axis, flattening the line along the bottom at 1% height.
  if (rounded < 1) return rounded
  // There are five ticks at quarter steps. Below 8 that leaves fractions, and
  // rounding them for a token axis produced duplicates (0 1 2 2 3); a multiple
  // of 4 divides cleanly.
  return rounded < 8 ? Math.max(4, Math.ceil(rounded / 4) * 4) : rounded
}
