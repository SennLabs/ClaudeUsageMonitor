import { describe, expect, it } from 'vitest'
import {
  fmtDateTime,
  fmtDay,
  fmtHour,
  fmtY,
  granularityFor,
  niceMax,
  parts,
  tooltipLabel,
  xLabel,
} from './chart'

describe('granularityFor', () => {
  it('buckets hourly only up to 48h, matching the server', () => {
    expect(granularityFor(24)).toBe('hour')
    expect(granularityFor(48)).toBe('hour')
    expect(granularityFor(49)).toBe('day')
    expect(granularityFor(168)).toBe('day')
    expect(granularityFor(720)).toBe('day')
  })

  it('treats the all-time sentinel as daily, not hourly', () => {
    // 0 is the API's "all time" value. Reading it as "less than 48" would
    // label hundreds of daily buckets as clock times, all identical.
    expect(granularityFor(0)).toBe('day')
  })

  it('falls back to hourly before the window is known', () => {
    expect(granularityFor(undefined)).toBe('hour')
  })
})

describe('bucket label parsing', () => {
  // The whole point of reading the string rather than constructing a Date:
  // these must produce the same label whatever zone the browser is in.
  it('labels a UTC bucket by its own characters', () => {
    expect(fmtDay('2026-09-10T00:00:00Z')).toBe('10 Sep')
    expect(fmtHour('2026-09-10T14:00:00Z')).toBe('14:00')
  })

  it('labels an offset bucket by its own characters, not the browser zone', () => {
    // 2026-09-10T00:00:00+08:00 is 2026-09-09T16:00Z. Through `new Date` this
    // rendered as "9 Sep" for a UTC viewer — a day off its own bucket.
    expect(fmtDay('2026-09-10T00:00:00+08:00')).toBe('10 Sep')
    expect(fmtHour('2026-09-10T09:00:00+08:00')).toBe('09:00')
    expect(fmtDateTime('2026-09-10T09:00:00+08:00')).toBe('10 Sep 09:00')
  })

  it('adds a two-digit year only when asked', () => {
    expect(fmtDay('2026-01-05T00:00:00Z')).toBe('5 Jan')
    expect(fmtDay('2026-01-05T00:00:00Z', true)).toBe('5 Jan 26')
  })

  it('drops the leading zero on the day but keeps it on the hour', () => {
    expect(fmtDay('2026-09-01T00:00:00Z')).toBe('1 Sep')
    expect(fmtHour('2026-09-01T07:00:00Z')).toBe('07:00')
  })

  it('exposes month as a zero-based index into MONTHS', () => {
    expect(parts('2026-12-31T23:00:00Z')).toEqual({
      year: 2026,
      month: 11,
      day: 31,
      hour: '23',
    })
  })

  it('returns the raw string rather than NaN for an unparseable bucket', () => {
    // A server that changes its bucket format should degrade to an ugly axis,
    // not to "NaN undefined" across every label.
    expect(parts('nonsense')).toBeNull()
    expect(fmtDay('nonsense')).toBe('nonsense')
    expect(fmtDateTime('nonsense')).toBe('nonsense')
    expect(fmtHour('nonsense')).toBe('??:00')
  })
})

describe('xLabel / tooltipLabel', () => {
  it('follows the granularity it is given', () => {
    expect(xLabel('2026-09-10T14:00:00Z', 'hour', false)).toBe('14:00')
    expect(xLabel('2026-09-10T00:00:00Z', 'day', false)).toBe('10 Sep')
    expect(xLabel('2026-09-10T00:00:00Z', 'day', true)).toBe('10 Sep 26')
  })

  it('gives the tooltip a full date, since it has the room', () => {
    expect(tooltipLabel('2026-09-10T14:00:00Z', 'hour')).toBe('10 Sep 14:00')
    expect(tooltipLabel('2026-09-10T00:00:00Z', 'day')).toBe('10 Sep 2026')
  })
})

describe('niceMax', () => {
  it('never returns 0, so an empty chart still has an axis', () => {
    expect(niceMax(0)).toBe(1)
  })

  it('rounds small maxima up to a multiple of 4', () => {
    // Five ticks at quarter steps. Below 8, anything not divisible by 4 leaves
    // fractional ticks, which rounded to duplicates on a token axis (0 1 2 2 3).
    expect(niceMax(1)).toBe(4)
    expect(niceMax(3)).toBe(4)
    expect(niceMax(5)).toBe(8)
    expect(niceMax(7)).toBe(8)
    expect(niceMax(9)).toBe(9)   // already >= 8, so no snap
  })

  it('rounds larger maxima up to one significant figure', () => {
    expect(niceMax(12)).toBe(20)
    expect(niceMax(51)).toBe(60)
    expect(niceMax(1200)).toBe(2000)
    expect(niceMax(100)).toBe(100)
  })

  it('keeps sub-unit cost maxima usable rather than snapping to 4', () => {
    expect(niceMax(0.04)).toBeCloseTo(0.04, 10)
    expect(niceMax(0.11)).toBeCloseTo(0.2, 10)
  })

  it('divides into four whole quarter-steps for every integer result', () => {
    for (const raw of [1, 3, 5, 9, 12, 51, 1200, 40000]) {
      const max = niceMax(raw)
      expect(max).toBeGreaterThanOrEqual(raw)
      const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max)
      expect(new Set(ticks.map(Math.round)).size).toBe(5)
    }
  })
})

describe('fmtY', () => {
  it('scales cost precision to the axis, so ticks are not all $0.00', () => {
    expect(fmtY(1.5, 'cost', 10)).toBe('$1.50')
    expect(fmtY(0.05, 'cost', 0.2)).toBe('$0.050')
    expect(fmtY(0.0025, 'cost', 0.01)).toBe('$0.0025')
  })

  it('abbreviates token counts and trims a trailing .0', () => {
    expect(fmtY(999, 'tokens', 1000)).toBe('999')
    expect(fmtY(1000, 'tokens', 4000)).toBe('1k')
    expect(fmtY(1500, 'tokens', 4000)).toBe('1.5k')
    expect(fmtY(2_400_000, 'tokens', 4_000_000)).toBe('2.4M')
  })

  it('rounds token ticks to integers — a fractional token is meaningless', () => {
    expect(fmtY(2.5, 'tokens', 10)).toBe('3')
  })
})
