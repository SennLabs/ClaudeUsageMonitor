import { createMemo, createResource, createSignal, For, onCleanup, onMount, Show } from 'solid-js'
import { fetchSessions, fetchSummary, fetchUsageOverTime, fetchUsageOverTimeByProject } from '../api'
import { formatCost, formatNumber } from '../format'
import { errorMessage, firstError, latest } from '../resource'
import { computeHourlyRate, loadSettings, WINDOW_HOURS, WINDOW_OPTIONS } from '../settings'
import type { TimeWindow } from '../settings'
import type { Metric } from './UsageChart'
import UsageChart from './UsageChart'

function isActive(lastSeenAt: string, windowMin: number) {
  return Date.now() - new Date(lastSeenAt).getTime() < windowMin * 60 * 1000
}

function timeAgo(iso: string) {
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  return `${Math.floor(secs / 3600)}h ago`
}

function ToggleBtn<T extends string>(props: {
  value: T
  current: T
  label: string
  onClick: (v: T) => void
}) {
  return (
    <button
      type="button"
      onClick={() => props.onClick(props.value)}
      class="px-3 py-1 text-xs font-medium rounded transition"
      classList={{
        'bg-slate-700 text-white': props.value === props.current,
        'text-slate-500 hover:text-slate-300': props.value !== props.current,
      }}
    >
      {props.label}
    </button>
  )
}

export default function TabletDashboard() {
  const settings = loadSettings()
  const REFRESH_MS = settings.refreshIntervalMs

  const [metric, setMetric] = createSignal<Metric>(settings.defaultMetric)
  const [groupBy, setGroupBy] = createSignal<'session' | 'project'>('session')
  const [timeWindow, setTimeWindow] = createSignal<TimeWindow>(settings.defaultTimeWindow)
  const hours = createMemo(() => WINDOW_HOURS[timeWindow()])
  const [countdown, setCountdown] = createSignal(REFRESH_MS / 1000)

  const [summary, { refetch: refetchSummary }] = createResource(fetchSummary)
  const [sessions, { refetch: refetchSessions }] = createResource(fetchSessions)
  const [usageTime, { refetch: refetchTime }] = createResource(timeWindow, (w) =>
    fetchUsageOverTime(WINDOW_HOURS[w]),
  )
  const [usageByProject, { refetch: refetchByProject }] = createResource(timeWindow, (w) =>
    fetchUsageOverTimeByProject(WINDOW_HOURS[w]),
  )

  let timer: ReturnType<typeof setInterval>
  let countdownTimer: ReturnType<typeof setInterval>

  onMount(() => {
    timer = setInterval(() => {
      refetchSummary()
      refetchSessions()
      refetchTime()
      refetchByProject()
      setCountdown(REFRESH_MS / 1000)
    }, REFRESH_MS)

    countdownTimer = setInterval(() => setCountdown((n) => Math.max(0, n - 1)), 1000)
  })

  onCleanup(() => {
    clearInterval(timer)
    clearInterval(countdownTimer)
  })

  const activeWindowMin = settings.activeSessionWindowMin

  const activeSessions = createMemo(() =>
    (latest(sessions) ?? []).filter((s) => isActive(s.last_seen_at, activeWindowMin)),
  )

  const costRatePerHour = createMemo(() => computeHourlyRate(latest(usageTime) ?? [], hours()))

  const alertActive = createMemo(() => {
    const t = settings.costAlertThresholdPerHour
    return t !== null && costRatePerHour() > t
  })

  const budget = settings.monthlyBudget

  const usedFraction = createMemo(() => {
    const s = latest(summary)
    if (!budget || !s) return null
    return Math.min(s.total_cost_usd / budget, 1)
  })

  const budgetBarColor = createMemo(() => {
    const f = usedFraction()
    if (f === null) return 'bg-emerald-500'
    if (f >= 0.9) return 'bg-red-500'
    if (f >= 0.7) return 'bg-amber-500'
    return 'bg-emerald-500'
  })

  const totalTokens = createMemo(() =>
    (latest(summary)?.total_input_tokens ?? 0) + (latest(summary)?.total_output_tokens ?? 0),
  )

  const apiError = createMemo(() =>
    firstError(summary, sessions, usageTime, usageByProject),
  )

  const chartProps = createMemo(() =>
    groupBy() === 'project'
      ? { projectData: latest(usageByProject), metric: metric(), hours: hours() }
      : { data: latest(usageTime), metric: metric(), hours: hours() },
  )

  return (
    <div class="min-h-screen bg-slate-950 text-white p-5 select-none">

      {/* Header */}
      <div class="flex items-center justify-between mb-5">
        <div class="flex items-center gap-3">
          <h1 class="text-2xl font-bold text-slate-100">Claude Usage</h1>
          <div class="flex items-center gap-1 rounded-lg border border-slate-800 overflow-hidden">
            <For each={WINDOW_OPTIONS}>
              {(opt) => (
                <ToggleBtn
                  value={opt.value}
                  current={timeWindow()}
                  label={opt.label}
                  onClick={setTimeWindow}
                />
              )}
            </For>
          </div>
          <div class="flex items-center gap-1 rounded-lg border border-slate-800 overflow-hidden">
            <ToggleBtn value="cost" current={metric()} label="Cost" onClick={setMetric} />
            <ToggleBtn value="tokens" current={metric()} label="Tokens" onClick={setMetric} />
          </div>
          <div class="flex items-center gap-1 rounded-lg border border-slate-800 overflow-hidden">
            <ToggleBtn value="session" current={groupBy()} label="Sessions" onClick={setGroupBy} />
            <ToggleBtn value="project" current={groupBy()} label="By project" onClick={setGroupBy} />
          </div>
        </div>
        <div class="flex items-center gap-4">
          <Show when={budget}>
            <a
              href="/settings"
              class="text-xs text-slate-600 hover:text-slate-400 transition"
            >
              Budget: ${budget}/mo
            </a>
          </Show>
          <Show when={!budget}>
            <a
              href="/settings"
              class="text-xs text-slate-600 hover:text-slate-400 transition"
            >
              ⚙ Set budget
            </a>
          </Show>
          <a
            href="/"
            class="text-xs text-slate-500 hover:text-slate-300 transition border border-slate-700 rounded px-2 py-1"
          >
            ← Full view
          </a>
          <div class="flex items-center gap-2 text-slate-600 text-xs">
            <span>{countdown()}s</span>
            <div class="h-1 w-16 rounded-full bg-slate-800 overflow-hidden">
              <div
                class="h-full bg-slate-600 transition-all duration-1000"
                style={{ width: `${(countdown() / (REFRESH_MS / 1000)) * 100}%` }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Alert banner */}
      <Show when={alertActive()}>
        <div class="mb-5 rounded-xl border border-amber-700 bg-amber-950 px-4 py-3 flex items-center gap-3">
          <span class="text-xl">⚠</span>
          <p class="text-sm font-semibold text-amber-300">
            Hourly spend rate ({formatCost(costRatePerHour())}) exceeds alert threshold
            (${settings.costAlertThresholdPerHour!.toFixed(2)}/hr)
          </p>
        </div>
      </Show>

      {/* Big stats */}
      <div class="grid grid-cols-3 gap-4 mb-5">
        <StatCard
          label="Active sessions"
          value={String(activeSessions().length)}
          accent="text-emerald-400"
          sub={`${latest(summary)?.total_sessions ?? '—'} total`}
        />
        <Show
          when={metric() === 'cost'}
          fallback={
            <StatCard
              label="Total tokens"
              value={totalTokens() >= 1_000_000
                ? `${(totalTokens() / 1_000_000).toFixed(1)}M`
                : `${(totalTokens() / 1_000).toFixed(0)}k`}
              accent="text-sky-400"
              sub={`${formatNumber(latest(summary)?.total_input_tokens ?? 0)} in · ${formatNumber(latest(summary)?.total_output_tokens ?? 0)} out`}
            />
          }
        >
          <StatCard
            label="Total spend"
            value={latest(summary) ? formatCost(latest(summary)!.total_cost_usd) : '—'}
            accent="text-sky-400"
            sub={`${formatNumber(totalTokens())} tokens`}
          />
        </Show>
        <StatCard
          label={metric() === 'cost' ? 'Rate (last 1h)' : 'Active models'}
          value={metric() === 'cost'
            ? formatCost(costRatePerHour())
            : String(new Set(activeSessions().flatMap((s) => (s.models ?? '').split(',').map((m) => m.trim()).filter(Boolean))).size)}
          accent={metric() === 'cost' && alertActive() ? 'text-amber-400' : 'text-violet-400'}
          sub={metric() === 'cost' ? (alertActive() ? '⚠ above threshold' : 'per hour') : 'in use'}
        />
      </div>

      {/* Budget bar */}
      <Show when={budget}>
        <div class="mb-5 rounded-xl bg-slate-900 border border-slate-800 px-4 py-3">
          <div class="flex items-center justify-between mb-1.5">
            <span class="text-xs font-medium text-slate-400">Monthly budget</span>
            <span class="text-xs text-slate-500">
              <Show when={usedFraction() !== null}>
                {(usedFraction()! * 100).toFixed(1)}% · {formatCost(latest(summary)?.total_cost_usd ?? 0)} of ${budget}
              </Show>
            </span>
          </div>
          <div class="h-2.5 rounded-full bg-slate-800 overflow-hidden">
            <div
              class={`h-full rounded-full transition-all duration-500 ${budgetBarColor()}`}
              style={{ width: `${((usedFraction() ?? 0) * 100).toFixed(1)}%` }}
            />
          </div>
        </div>
      </Show>

      {/* Chart */}
      <div class="mb-5 rounded-xl bg-slate-900 border border-slate-800 overflow-hidden">
        <UsageChart {...chartProps()} />
      </div>

      {/* Active sessions / project list */}
      <div class="rounded-xl bg-slate-900 border border-slate-800 overflow-hidden">
        <div class="px-4 py-3 border-b border-slate-800">
          <h2 class="text-xs font-semibold text-slate-500 uppercase tracking-wide">
            {groupBy() === 'project' ? 'By project (active)' : 'Active sessions'}
          </h2>
        </div>

        <Show
          when={groupBy() === 'project'}
          fallback={
            <Show
              when={activeSessions().length > 0}
              fallback={<p class="p-6 text-center text-slate-600 text-sm">No active sessions.</p>}
            >
              <div class="divide-y divide-slate-800">
                <For each={activeSessions()}>
                  {(s) => (
                    <div class="flex items-center justify-between px-4 py-3">
                      <div class="flex items-center gap-3">
                        <span class="h-2 w-2 rounded-full bg-emerald-500 flex-shrink-0" />
                        <div>
                          <span class="font-mono text-xs text-slate-300">{s.session_id.slice(0, 16)}</span>
                          <Show when={s.project_name}>
                            <span class="ml-2 rounded bg-sky-900 px-1.5 py-0.5 text-xs text-sky-300">
                              {s.project_name}
                            </span>
                          </Show>
                        </div>
                      </div>
                      <div class="flex items-center gap-4 text-sm text-slate-400">
                        <span class="text-xs">{s.models ?? '—'}</span>
                        <span class="text-sky-400 font-medium tabular-nums">{formatCost(s.cost_usd)}</span>
                        <span class="text-slate-600 text-xs w-16 text-right">{timeAgo(s.last_seen_at)}</span>
                      </div>
                    </div>
                  )}
                </For>
              </div>
            </Show>
          }
        >
          {/* Project grouped view */}
          {(() => {
            const projectMap = createMemo(() => {
              const map = new Map<string, { cost: number; active: number; sessions: number }>()
              for (const s of latest(sessions) ?? []) {
                const key = s.project_name ?? '(untagged)'
                const cur = map.get(key) ?? { cost: 0, active: 0, sessions: 0 }
                map.set(key, {
                  cost: cur.cost + s.cost_usd,
                  active: cur.active + (isActive(s.last_seen_at, activeWindowMin) ? 1 : 0),
                  sessions: cur.sessions + 1,
                })
              }
              return [...map.entries()].sort((a, b) => b[1].cost - a[1].cost)
            })

            return (
              <Show
                when={projectMap().length > 0}
                fallback={<p class="p-6 text-center text-slate-600 text-sm">No data yet.</p>}
              >
                <div class="divide-y divide-slate-800">
                  <For each={projectMap()}>
                    {([name, stats]) => (
                      <div class="flex items-center justify-between px-4 py-3">
                        <div class="flex items-center gap-3">
                          <Show when={stats.active > 0}>
                            <span class="h-2 w-2 rounded-full bg-emerald-500 flex-shrink-0" />
                          </Show>
                          <Show when={stats.active === 0}>
                            <span class="h-2 w-2 rounded-full bg-slate-700 flex-shrink-0" />
                          </Show>
                          <span class="text-sm text-slate-300">{name}</span>
                        </div>
                        <div class="flex items-center gap-6 text-sm text-slate-400">
                          <span class="text-xs">{stats.sessions} sessions</span>
                          <Show when={stats.active > 0}>
                            <span class="text-xs text-emerald-500">{stats.active} active</span>
                          </Show>
                          <span class="text-sky-400 font-medium tabular-nums">{formatCost(stats.cost)}</span>
                        </div>
                      </div>
                    )}
                  </For>
                </div>
              </Show>
            )
          })()}
        </Show>
      </div>

      <Show when={apiError()}>
        {(err) => (
          <div class="fixed right-4 bottom-4 max-w-sm rounded-lg bg-red-600 px-4 py-3 text-sm text-white shadow-lg">
            <p class="font-medium">Couldn't reach the usage API</p>
            <p class="mt-0.5 text-red-100">{errorMessage(err())}</p>
          </div>
        )}
      </Show>
    </div>
  )
}

function StatCard(props: { label: string; value: string; accent: string; sub?: string }) {
  return (
    <div class="rounded-xl bg-slate-900 border border-slate-800 p-5">
      <p class="text-xs font-medium tracking-wide text-slate-500 uppercase mb-1">{props.label}</p>
      <p class={`text-5xl font-bold tabular-nums ${props.accent}`}>{props.value}</p>
      <Show when={props.sub}>
        <p class="mt-1 text-xs text-slate-600">{props.sub}</p>
      </Show>
    </div>
  )
}
