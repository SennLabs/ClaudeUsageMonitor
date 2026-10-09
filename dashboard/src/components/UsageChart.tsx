import { createMemo, createSignal, For, onCleanup, onMount, Show } from 'solid-js'
import type { ProjectTimePoint, UsageTimePoint } from '../api'
import { fmtY, granularityFor, niceMax, tooltipLabel, xLabel } from '../chart'

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
const H = 220
const PH = H - MT - MB

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
  /** Drop the card background/border so a host can supply its own. */
  bare?: boolean
}

export default function UsageChart(props: UsageChartProps) {
  const [hoveredSeries, setHoveredSeries] = createSignal<number | null>(null)
  const [hoveredBucket, setHoveredBucket] = createSignal<string | null>(null)

  // The plot is drawn at the container's real pixel width rather than scaled
  // from a fixed 800-wide viewBox. Scaling made a wide monitor render the
  // chart several hundred pixels tall with oversized axis text.
  const [width, setWidth] = createSignal(800)
  const W = () => width()
  const PW = () => Math.max(W() - ML - MR, 1)
  let container!: HTMLDivElement
  onMount(() => {
    const ro = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width)
      if (w > 0) setWidth(w)
    })
    ro.observe(container)
    onCleanup(() => ro.disconnect())
  })
  const xAt = (i: number, n: number) => ML + (i / Math.max(n - 1, 1)) * PW()

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
        x: xAt(i, n),
        y: MT + (1 - (byBucket.get(b) ?? 0) / maxVal()) * PH,
        bucket: b,
        value: byBucket.get(b) ?? 0,
      }))
      const line = coords.map((c, i) => `${i === 0 ? 'M' : 'L'}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ')
      // With one bucket every point sits at x = ML, so the line renders as
      // nothing and the area becomes a triangle spanning the whole plot —
      // a fresh install with an hour of data looked like a day of ramping
      // spend. Draw just the dot instead.
      const area = n > 1
        ? `${line} L${(ML + PW()).toFixed(1)},${(MT + PH).toFixed(1)} L${ML},${(MT + PH).toFixed(1)} Z`
        : ''
      return { ...s, coords, line, area }
    })
  })

  const yTicks = createMemo(() =>
    [0, 0.25, 0.5, 0.75, 1].map((f) => ({
      y: MT + (1 - f) * PH,
      label: fmtY(f * maxVal(), props.metric, maxVal()),
    })),
  )

  const xTicks = createMemo(() => {
    const buckets = allBuckets()
    if (buckets.length === 0) return []
    // Roughly one label per 110px so a wide chart gets more of them.
    const target = Math.max(2, Math.floor(PW() / 110))
    const step = Math.max(1, Math.ceil(buckets.length / target))
    return buckets
      .map((b, i) => ({ b, i }))
      // Always label the last bucket, dropping a stepped label that would sit on top of it.
      .filter(({ i }) => i === buckets.length - 1 || (i % step === 0 && buckets.length - 1 - i >= step / 2))
      .map(({ b, i }) => ({
        x: xAt(i, buckets.length),
        label: xLabel(b, granularity(), multiYear()),
      }))
  })

  // Hover is resolved from the pointer position in one handler. It used to be
  // per-bucket hit rects plus per-point circles, but those captured their x
  // position when first created and <For> reuses them by bucket key, so after
  // a refresh added a bucket they sat over the wrong part of the plot; the
  // multi-series circles also overlapped neighbouring columns.
  function onPointerMove(e: PointerEvent) {
    const buckets = allBuckets()
    const n = buckets.length
    if (n === 0) return
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
    const px = ((e.clientX - rect.left) / rect.width) * W()
    const py = ((e.clientY - rect.top) / rect.height) * H
    if (px < ML - 8 || px > ML + PW() + 8 || py < MT - 8 || py > MT + PH + 8) {
      setHoveredBucket(null)
      setHoveredSeries(null)
      return
    }
    const idx = n === 1 ? 0 : Math.min(n - 1, Math.max(0, Math.round(((px - ML) / PW()) * (n - 1))))
    setHoveredBucket(buckets[idx])
    if (!isMulti()) {
      setHoveredSeries(null)
      return
    }
    // Nearest line vertically at that bucket.
    let best = 0
    let bestDist = Infinity
    seriesPaths().forEach((sp, si) => {
      const d = Math.abs(sp.coords[idx].y - py)
      if (d < bestDist) {
        bestDist = d
        best = si
      }
    })
    setHoveredSeries(best)
  }

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
    <div
      ref={container}
      classList={{
        'rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900':
          !props.bare,
      }}
    >
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
          viewBox={`0 0 ${W()} ${H}`}
          width="100%"
          height={H}
          class="block touch-none"
          onPointerMove={onPointerMove}
          onPointerDown={onPointerMove}
          onPointerLeave={() => { setHoveredBucket(null); setHoveredSeries(null) }}
        >
          {/* Y grid + labels */}
          <For each={yTicks()}>
            {(tick) => (
              <>
                <line x1={ML} x2={ML + PW()} y1={tick.y} y2={tick.y}
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
                <Show when={!isMulti() && sp.area !== ''}>
                  <path d={sp.area} fill={sp.color} fill-opacity="0.12" />
                </Show>

                {/* Line */}
                <path d={sp.line} fill="none" stroke={sp.color}
                  stroke-width={hoveredSeries() === si() ? 2.5 : 2}
                  stroke-opacity={isMulti() && hoveredSeries() !== null && hoveredSeries() !== si() ? 0.25 : 1}
                  stroke-linejoin="round" stroke-linecap="round" />

                {/* Dots */}
                <For each={showDots() || sp.coords.length === 1 ? sp.coords : sp.coords.filter((c) => c.bucket === hoveredBucket())}>
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
              // Accessor, not a captured value: a non-keyed <Show> runs this
              // callback once, so a computed x froze the crosshair in place.
              const x = () => xAt(allBuckets().indexOf(b()), allBuckets().length)
              return (
                <line x1={x()} x2={x()} y1={MT} y2={MT + PH}
                  stroke="currentColor" stroke-opacity="0.2"
                  stroke-width="1" stroke-dasharray="3 3" />
              )
            }}
          </Show>

        </svg>
      </Show>
    </div>
  )
}
