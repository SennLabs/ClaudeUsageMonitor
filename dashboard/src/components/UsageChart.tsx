import { createMemo, createSignal, For, Show } from 'solid-js'
import type { ProjectTimePoint, UsageTimePoint } from '../api'

export type Metric = 'cost' | 'tokens'

// Up to 8 named project series get distinct colours
const SERIES_COLORS = [
  '#10b981', // emerald
  '#38bdf8', // sky
  '#a78bfa', // violet
  '#fbbf24', // amber
  '#f472b6', // pink
  '#2dd4bf', // teal
  '#fb923c', // orange
  '#818cf8', // indigo
]

const ML = 68
const MR = 16
const MT = 16
const MB = 44
const W = 800
const H = 220
const PW = W - ML - MR
const PH = H - MT - MB

// Bucket granularity mirrors the server's _time_bucket_fmt: hourly up to 48h,
// daily beyond that and for all-time (hours = 0). Labelling has to follow it —
// formatting a daily bucket as a clock time renders every point identically.
type Granularity = 'hour' | 'day'

function granularityFor(hours: number | undefined): Granularity {
  if (hours === undefined) return 'hour'
  return hours > 0 && hours <= 48 ? 'hour' : 'day'
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function fmtHour(iso: string) {
  const d = new Date(iso)
  return `${d.getHours().toString().padStart(2, '0')}:00`
}
// Daily buckets are UTC day starts, so they're named in UTC. Rendering them in
// local time would shift the date by a day for anyone west of Greenwich.
function fmtDay(iso: string, withYear = false) {
  const d = new Date(iso)
  const base = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`
  return withYear ? `${base} ${String(d.getUTCFullYear()).slice(2)}` : base
}
function fmtDateTime(iso: string) {
  const d = new Date(iso)
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getHours().toString().padStart(2, '0')}:00`
}
function xLabel(iso: string, g: Granularity, multiYear: boolean) {
  return g === 'hour' ? fmtHour(iso) : fmtDay(iso, multiYear)
}
function tooltipLabel(iso: string, g: Granularity) {
  if (g === 'hour') return fmtDateTime(iso)
  const d = new Date(iso)
  return `${fmtDay(iso)} ${d.getUTCFullYear()}`
}
function fmtY(v: number, metric: Metric) {
  if (metric === 'cost') return `$${v.toFixed(2)}`
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}k`
  return String(v)
}
function niceMax(raw: number) {
  if (raw === 0) return 1
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  return Math.ceil(raw / mag) * mag
}

interface Series {
  name: string
  color: string
  points: { bucket: string; value: number }[]
}

// ── props ──────────────────────────────────────────────────────────────────
export interface UsageChartProps {
  // single-series mode
  data?: UsageTimePoint[]
  // grouped-by-project mode (takes precedence when provided)
  projectData?: ProjectTimePoint[]
  metric: Metric
  hours?: number
}

export default function UsageChart(props: UsageChartProps) {
  const [hoveredSeries, setHoveredSeries] = createSignal<number | null>(null)
  const [hoveredBucket, setHoveredBucket] = createSignal<string | null>(null)

  // Derive normalised series from whichever data prop was given
  const series = createMemo<Series[]>(() => {
    if (props.projectData && props.projectData.length > 0) {
      const byProject = new Map<string, { bucket: string; value: number }[]>()
      for (const pt of props.projectData) {
        const v = props.metric === 'cost' ? pt.cost_usd : pt.total_tokens
        if (!byProject.has(pt.project_name)) byProject.set(pt.project_name, [])
        byProject.get(pt.project_name)!.push({ bucket: pt.bucket, value: v })
      }
      // Sort projects by total value desc, cap at 8
      return Array.from(byProject.entries())
        .map(([name, points]) => ({ name, total: points.reduce((s, p) => s + p.value, 0), points }))
        .sort((a, b) => b.total - a.total)
        .slice(0, 8)
        .map(({ name, points }, i) => ({
          name,
          color: SERIES_COLORS[i],
          points: [...points].sort((a, b) => a.bucket.localeCompare(b.bucket)),
        }))
    }

    const data = props.data ?? []
    return [
      {
        name: props.metric === 'cost' ? 'Cost' : 'Tokens',
        color: SERIES_COLORS[0],
        points: data.map((p) => ({
          bucket: p.bucket,
          value: props.metric === 'cost' ? p.cost_usd : p.total_tokens,
        })),
      },
    ]
  })

  const allBuckets = createMemo(() => {
    const set = new Set<string>()
    for (const s of series()) s.points.forEach((p) => set.add(p.bucket))
    return [...set].sort()
  })

  const granularity = createMemo(() => granularityFor(props.hours))

  // Only disambiguate with a year when the window actually crosses one —
  // realistically only the all-time view.
  const multiYear = createMemo(() => {
    const b = allBuckets()
    if (b.length === 0) return false
    return b[0].slice(0, 4) !== b[b.length - 1].slice(0, 4)
  })

  // All-time can return hundreds of daily buckets; per-point circles stop being
  // legible (and stop being cheap) long before that.
  const showDots = createMemo(() => allBuckets().length <= 60)

  const maxVal = createMemo(() =>
    niceMax(Math.max(...series().flatMap((s) => s.points.map((p) => p.value)), 0)),
  )

  const isMulti = createMemo(() => series().length > 1)

  // Build per-series SVG paths aligned to allBuckets
  const seriesPaths = createMemo(() => {
    const buckets = allBuckets()
    const n = buckets.length
    if (n === 0) return []

    return series().map((s) => {
      const byBucket = new Map(s.points.map((p) => [p.bucket, p.value]))
      const coords = buckets.map((b, i) => ({
        x: ML + (i / Math.max(n - 1, 1)) * PW,
        y: MT + (1 - (byBucket.get(b) ?? 0) / maxVal()) * PH,
        bucket: b,
        value: byBucket.get(b) ?? 0,
      }))
      const line = coords.map((c, i) => `${i === 0 ? 'M' : 'L'}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ')
      const area = `${line} L${(ML + PW).toFixed(1)},${(MT + PH).toFixed(1)} L${ML},${(MT + PH).toFixed(1)} Z`
      return { ...s, coords, line, area }
    })
  })

  const yTicks = createMemo(() =>
    [0, 0.25, 0.5, 0.75, 1].map((f) => ({
      y: MT + (1 - f) * PH,
      label: fmtY(f * maxVal(), props.metric),
    })),
  )

  const xTicks = createMemo(() => {
    const buckets = allBuckets()
    if (buckets.length === 0) return []
    const step = Math.max(1, Math.floor(buckets.length / 7))
    return buckets
      .filter((_, i) => i % step === 0 || i === buckets.length - 1)
      .map((b) => {
        const idx = allBuckets().indexOf(b)
        const n = allBuckets().length
        return {
          x: ML + (idx / Math.max(n - 1, 1)) * PW,
          label: xLabel(b, granularity(), multiYear()),
        }
      })
  })

  // Hover tooltip
  const hoveredInfo = createMemo(() => {
    const b = hoveredBucket()
    const si = hoveredSeries()
    if (!b) return null
    if (si !== null) {
      const sp = seriesPaths()[si]
      if (!sp) return null
      const coord = sp.coords.find((c) => c.bucket === b)
      if (!coord) return null
      return { name: sp.name, color: sp.color, value: coord.value, bucket: b }
    }
    // In single-series mode, find value across the single series
    const sp = seriesPaths()[0]
    const coord = sp?.coords.find((c) => c.bucket === b)
    if (!coord) return null
    return { name: sp.name, color: sp.color, value: coord.value, bucket: b }
  })

  const hasData = createMemo(() => allBuckets().length > 0)

  return (
    <div class="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div class="flex items-center justify-between px-4 pt-3 pb-2 flex-wrap gap-2">
        <h3 class="text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
          {props.metric === 'cost' ? 'Cost' : 'Tokens'} over time
        </h3>
        <Show when={hoveredInfo()}>
          {(info) => (
            <span class="text-xs text-slate-500 dark:text-slate-400">
              <Show when={isMulti()}>
                <span class="mr-1 font-medium" style={{ color: info().color }}>
                  {info().name}
                </span>
                {'·'}{' '}
              </Show>
              {tooltipLabel(info().bucket, granularity())} —{' '}
              <span class="font-semibold" style={{ color: info().color }}>
                {props.metric === 'cost' ? `$${info().value.toFixed(2)}` : info().value.toLocaleString()}
              </span>
            </span>
          )}
        </Show>
      </div>

      {/* Legend for multi-series */}
      <Show when={isMulti()}>
        <div class="flex flex-wrap gap-3 px-4 pb-2">
          <For each={series()}>
            {(s) => (
              <span class="flex items-center gap-1 text-xs text-slate-600 dark:text-slate-400">
                <span class="inline-block h-2 w-4 rounded-sm" style={{ background: s.color }} />
                {s.name}
              </span>
            )}
          </For>
        </div>
      </Show>

      <Show
        when={hasData()}
        fallback={
          <p class="py-12 text-center text-sm text-slate-400 dark:text-slate-600">
            No usage data in this window yet.
          </p>
        }
      >
        <svg
          viewBox={`0 0 ${W} ${H}`}
          class="w-full"
          onMouseLeave={() => { setHoveredBucket(null); setHoveredSeries(null) }}
        >
          {/* Y grid + labels */}
          <For each={yTicks()}>
            {(tick) => (
              <>
                <line x1={ML} x2={ML + PW} y1={tick.y} y2={tick.y}
                  stroke="currentColor" stroke-opacity="0.08" />
                <text x={ML - 6} y={tick.y + 4} text-anchor="end"
                  fill="currentColor" fill-opacity="0.4" font-size="9">
                  {tick.label}
                </text>
              </>
            )}
          </For>

          {/* X labels */}
          <For each={xTicks()}>
            {(tick) => (
              <text x={tick.x} y={H - MB + 16} text-anchor="middle"
                fill="currentColor" fill-opacity="0.4" font-size="9">
                {tick.label}
              </text>
            )}
          </For>

          {/* Series: area + line + dots */}
          <For each={seriesPaths()}>
            {(sp, si) => (
              <>
                {/* Area fill only for single-series */}
                <Show when={!isMulti()}>
                  <path d={sp.area} fill={sp.color} fill-opacity="0.12" />
                </Show>

                {/* Line */}
                <path d={sp.line} fill="none" stroke={sp.color}
                  stroke-width={hoveredSeries() === si() ? 2.5 : 2}
                  stroke-opacity={isMulti() && hoveredSeries() !== null && hoveredSeries() !== si() ? 0.25 : 1}
                  stroke-linejoin="round" stroke-linecap="round" />

                {/* Dots */}
                <For each={showDots() ? sp.coords : sp.coords.filter((c) => c.bucket === hoveredBucket())}>
                  {(c) => (
                    <circle cx={c.x} cy={c.y}
                      r={hoveredBucket() === c.bucket && (!isMulti() || hoveredSeries() === si()) ? 5 : 3}
                      fill={sp.color} stroke="white" stroke-width="1.5" />
                  )}
                </For>
              </>
            )}
          </For>

          {/* Crosshair line */}
          <Show when={hoveredBucket()}>
            {(b) => {
              const idx = allBuckets().indexOf(b())
              const x = ML + (idx / Math.max(allBuckets().length - 1, 1)) * PW
              return (
                <line x1={x} x2={x} y1={MT} y2={MT + PH}
                  stroke="currentColor" stroke-opacity="0.2"
                  stroke-width="1" stroke-dasharray="3 3" />
              )
            }}
          </Show>

          {/* Invisible hit rects per bucket column */}
          <For each={allBuckets()}>
            {(b, bi) => {
              const n = allBuckets().length
              const x = ML + (bi() / Math.max(n - 1, 1)) * PW
              const colW = PW / Math.max(n - 1, 1)
              return (
                <rect x={x - colW / 2} y={MT} width={colW} height={PH}
                  fill="transparent"
                  onMouseEnter={() => setHoveredBucket(b)} />
              )
            }}
          </For>

          {/* Per-series hit rects (only in multi mode, to identify which line) */}
          <Show when={isMulti() && showDots()}>
            <For each={seriesPaths()}>
              {(sp, si) => (
                <For each={sp.coords}>
                  {(c) => (
                    <circle cx={c.x} cy={c.y} r={8} fill="transparent"
                      onMouseEnter={() => { setHoveredBucket(c.bucket); setHoveredSeries(si()) }} />
                  )}
                </For>
              )}
            </For>
          </Show>
        </svg>
      </Show>
    </div>
  )
}
