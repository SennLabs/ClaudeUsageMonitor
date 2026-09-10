import { createMemo, createResource, createSignal, onCleanup, onMount, Show } from 'solid-js'
import {
  fetchSessions,
  fetchSummary,
  fetchUsageByModel,
  fetchUsageOverTime,
  fetchUsageOverTimeByProject,
} from './api'
import ModelBreakdown from './components/ModelBreakdown'
import SessionsTable from './components/SessionsTable'
import SummaryCards from './components/SummaryCards'
import ThemeToggle from './components/ThemeToggle'
import UsageChart, { type Metric } from './components/UsageChart'
import { computeHourlyRate, loadSettings, WINDOW_HOURS, WINDOW_OPTIONS } from './settings'
import type { TimeWindow } from './settings'

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
  const settings = loadSettings()
  const [metric, setMetric] = createSignal<Metric>(settings.defaultMetric)
  const [groupBy, setGroupBy] = createSignal<'session' | 'project'>('session')
  const [timeWindow, setTimeWindow] = createSignal<TimeWindow>(settings.defaultTimeWindow)
  const hours = createMemo(() => WINDOW_HOURS[timeWindow()])

  const [summary, { refetch: refetchSummary }] = createResource(fetchSummary)
  const [sessions, { refetch: refetchSessions }] = createResource(fetchSessions)
  const [usageByModel, { refetch: refetchUsage }] = createResource(fetchUsageByModel)
  const [usageOverTime, { refetch: refetchTime }] = createResource(timeWindow, (w) =>
    fetchUsageOverTime(WINDOW_HOURS[w]),
  )
  const [usageByProject, { refetch: refetchByProject }] = createResource(timeWindow, (w) =>
    fetchUsageOverTimeByProject(WINDOW_HOURS[w]),
  )

  let timer: ReturnType<typeof setInterval>

  onMount(() => {
    timer = setInterval(
      () => {
        refetchSummary()
        refetchSessions()
        refetchUsage()
        refetchTime()
        refetchByProject()
      },
      settings.refreshIntervalMs,
    )
  })

  onCleanup(() => clearInterval(timer))

  const costRatePerHour = createMemo(() =>
    computeHourlyRate(usageOverTime() ?? [], hours()),
  )

  const alertActive = createMemo(() => {
    const t = settings.costAlertThresholdPerHour
    return t !== null && costRatePerHour() > t
  })

  const chartProps = createMemo(() =>
    groupBy() === 'project'
      ? { projectData: usageByProject(), metric: metric(), hours: hours() }
      : { data: usageOverTime(), metric: metric(), hours: hours() },
  )

  return (
    <div class="min-h-screen">
      <header class="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <h1 class="text-lg font-semibold text-slate-900 dark:text-slate-50">Claude Usage Monitor</h1>
          <div class="flex items-center gap-3">
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
            (${settings.costAlertThresholdPerHour!.toFixed(2)}/hr)
          </p>
        </div>
      </Show>

      <main class="mx-auto max-w-6xl space-y-8 px-6 py-8">
        <section>
          <SummaryCards summary={summary()} />
        </section>

        <section>
          {/* Chart controls */}
          <div class="mb-3 flex items-center justify-between gap-3 flex-wrap">
            <div class="flex items-center gap-2">
              <ToggleGroup value={timeWindow()} options={WINDOW_OPTIONS} onChange={setTimeWindow} />
              <ToggleGroup
                value={metric()}
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
          <SessionsTable sessions={sessions()} groupByProject={groupBy() === 'project'} />
        </section>

        <section>
          <h2 class="mb-3 text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
            Usage by model
          </h2>
          <ModelBreakdown usage={usageByModel()} />
        </section>
      </main>

      {(summary.error || sessions.error || usageByModel.error) && (
        <div class="fixed right-4 bottom-4 rounded-lg bg-red-600 px-4 py-2 text-sm text-white shadow-lg">
          Couldn't reach the usage API — is the backend running?
        </div>
      )}
    </div>
  )
}
