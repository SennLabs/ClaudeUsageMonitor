import { createEffect, createMemo, createResource, createSignal, onCleanup, Show } from 'solid-js'
import {
  fetchSessions,
  fetchSummary,
  fetchUsageByModel,
  fetchUsageOverTime,
  fetchUsageOverTimeByProject,
  fetchSpendRate,
} from './api'
import { formatCost, formatNumber } from './format'
import ModelBreakdown from './components/ModelBreakdown'
import SessionsTable from './components/SessionsTable'
import SummaryCards from './components/SummaryCards'
import ThemeToggle from './components/ThemeToggle'
import UsageChart, { type Metric } from './components/UsageChart'
import { errorMessage, firstError, latest } from './resource'
import { WINDOW_HOURS, WINDOW_OPTIONS } from './settings'
import type { TimeWindow } from './settings'
import { settings } from './settingsStore'

function ToggleGroup<T extends string>(props: {
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div class="flex rounded-lg border border-slate-300 overflow-hidden dark:border-slate-700">
      {props.options.map((opt) => (
        <button
          type="button"
          onClick={() => props.onChange(opt.value)}
          class="px-3 py-1 text-xs font-medium transition"
          classList={{
            'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900': props.value === opt.value,
            'bg-white text-slate-600 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-400 dark:hover:bg-slate-800':
              props.value !== opt.value,
          }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

export default function App() {
  const [metric, setMetric] = createSignal<Metric | null>(null)
  const [groupBy, setGroupBy] = createSignal<'session' | 'project'>('session')
  // Set while a row in SessionsTable is being edited. <For> keys by reference,
  // so a refetch rebuilds every row and destroys the open input — on iOS the
  // keyboard closes and cannot reopen, making the field untypeable.
  const [editing, setEditing] = createSignal(false)
  const [timeWindow, setTimeWindow] = createSignal<TimeWindow | null>(null)

  // Null until the user picks one, so the server-configured default applies as
  // soon as settings load rather than being frozen at first render.
  const activeMetric = () => metric() ?? settings().defaultMetric
  const activeWindow = () => timeWindow() ?? settings().defaultTimeWindow
  const hours = createMemo(() => WINDOW_HOURS[activeWindow()])

  const [summary, { refetch: refetchSummary }] = createResource(fetchSummary)
  const [spendRate, { refetch: refetchRate }] = createResource(fetchSpendRate)
  const [sessions, { refetch: refetchSessions }] = createResource(fetchSessions)
  const [usageByModel, { refetch: refetchUsage }] = createResource(fetchUsageByModel)
  const [usageOverTime, { refetch: refetchTime }] = createResource(activeWindow, (w) =>
    fetchUsageOverTime(WINDOW_HOURS[w]),
  )
  const [usageByProject, { refetch: refetchByProject }] = createResource(activeWindow, (w) =>
    fetchUsageOverTimeByProject(WINDOW_HOURS[w]),
  )

  // An effect rather than onMount, so changing the interval in Settings takes
  // effect immediately instead of on the next page load.
  createEffect(() => {
    const timer = setInterval(() => {
      if (editing()) return
      refetchSummary()
      refetchRate()
      refetchSessions()
      refetchUsage()
      refetchTime()
      refetchByProject()
    }, settings().refreshIntervalMs)
    onCleanup(() => clearInterval(timer))
  })

  const costRatePerHour = () => latest(spendRate)?.cost_usd_per_hour ?? 0

  const alertActive = createMemo(() => {
    const t = settings().costAlertThresholdPerHour
    return t !== null && costRatePerHour() > t
  })

  const apiError = createMemo(() =>
    firstError(summary, spendRate, sessions, usageByModel, usageOverTime, usageByProject),
  )

  const chartProps = createMemo(() =>
    groupBy() === 'project'
      ? { projectData: latest(usageByProject), metric: activeMetric(), hours: hours() }
      : { data: latest(usageOverTime), metric: activeMetric(), hours: hours() },
  )

  return (
    <div class="min-h-screen">
      <header class="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <h1 class="text-lg font-semibold text-slate-900 dark:text-slate-50">Claude Usage Monitor</h1>
          <div class="flex items-center gap-3">
            <a
              href="/insights"
              class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              Insights
            </a>
            <a
              href="/users"
              class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              Users
            </a>
            <a
              href="/tablet"
              class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              Tablet view
            </a>
            <a
              href="/settings"
              class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              ⚙ Settings
            </a>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <Show when={alertActive()}>
        <div class="border-b border-amber-200 bg-amber-50 px-6 py-2.5 dark:border-amber-800 dark:bg-amber-950">
          <p class="mx-auto max-w-6xl text-sm font-medium text-amber-800 dark:text-amber-300">
            ⚠ Hourly spend rate (${costRatePerHour().toFixed(2)}) has exceeded your alert threshold
            (${settings().costAlertThresholdPerHour!.toFixed(2)}/hr)
          </p>
        </div>
      </Show>

      <main class="mx-auto max-w-6xl space-y-8 px-6 py-8">
        <section>
          <SummaryCards summary={latest(summary)} />
          <Show when={(latest(summary)?.unattributed_events ?? 0) > 0}>
            <p class="mt-2 text-xs text-slate-500 dark:text-slate-400">
              {formatNumber(latest(summary)!.unattributed_events)} event(s) worth{' '}
              {formatCost(latest(summary)!.unattributed_cost_usd)} arrived without a session ID.
              They are counted in the totals above but appear in no per-session or per-user view.
            </p>
          </Show>
        </section>

        <section>
          {/* Chart controls */}
          <div class="mb-3 flex items-center justify-between gap-3 flex-wrap">
            <div class="flex items-center gap-2">
              <ToggleGroup value={activeWindow()} options={WINDOW_OPTIONS} onChange={setTimeWindow} />
              <ToggleGroup
                value={activeMetric()}
                options={[
                  { value: 'cost', label: 'Cost' },
                  { value: 'tokens', label: 'Tokens' },
                ]}
                onChange={setMetric}
              />
            </div>
            <ToggleGroup
              value={groupBy()}
              options={[
                { value: 'session', label: 'All sessions' },
                { value: 'project', label: 'By project' },
              ]}
              onChange={setGroupBy}
            />
          </div>
          <UsageChart {...chartProps()} />
        </section>

        <section>
          <div class="mb-3 flex items-center justify-between gap-3 flex-wrap">
            <h2 class="text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
              Sessions
            </h2>
            <ToggleGroup
              value={groupBy()}
              options={[
                { value: 'session', label: 'By session' },
                { value: 'project', label: 'By project' },
              ]}
              onChange={setGroupBy}
            />
          </div>
          <SessionsTable
            sessions={latest(sessions)}
            groupByProject={groupBy() === 'project'}
            onEditingChange={setEditing}
          />
        </section>

        <section>
          <h2 class="mb-3 text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
            Usage by model
          </h2>
          <ModelBreakdown usage={latest(usageByModel)} />
        </section>
      </main>

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
