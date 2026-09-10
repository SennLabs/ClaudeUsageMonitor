import { createEffect, createResource, createSignal, For, onCleanup, Show } from 'solid-js'
import type { AttributionRow } from '../api'
import {
  fetchAttribution,
  fetchErrorStats,
  fetchFleet,
  fetchLatency,
  fetchToolStats,
} from '../api'
import { formatCost, formatNumber, formatTime } from '../format'
import { errorMessage, firstError, latest } from '../resource'
import { WINDOW_HOURS, WINDOW_OPTIONS } from '../settings'
import type { TimeWindow } from '../settings'
import { settings } from '../settingsStore'
import ThemeToggle from './ThemeToggle'

function Panel(props: { title: string; hint?: string; children: any }) {
  return (
    <section class="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
      <h2 class="text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
        {props.title}
      </h2>
      <Show when={props.hint}>
        <p class="mt-1 text-xs text-slate-400 dark:text-slate-500">{props.hint}</p>
      </Show>
      <div class="mt-3">{props.children}</div>
    </section>
  )
}

function Stat(props: { label: string; value: string; accent?: string }) {
  return (
    <div>
      <p class="text-xs text-slate-500 dark:text-slate-400">{props.label}</p>
      <p class={`mt-0.5 text-xl font-semibold ${props.accent ?? 'text-slate-900 dark:text-slate-50'}`}>
        {props.value}
      </p>
    </div>
  )
}

function Empty(props: { children: any }) {
  return <p class="py-4 text-center text-sm text-slate-400 dark:text-slate-600">{props.children}</p>
}

/** Cost ranked within one dimension, with a bar for relative share. */
function CostBars(props: { rows: AttributionRow[] | undefined; empty: string }) {
  const rows = () => props.rows ?? []
  const max = () => Math.max(...rows().map((r) => r.cost_usd), 0.000001)
  return (
    <Show when={rows().length > 0} fallback={<Empty>{props.empty}</Empty>}>
      <div class="space-y-1.5">
        <For each={rows()}>
          {(row) => (
            <div>
              <div class="flex items-baseline justify-between text-sm">
                <span class="font-mono text-xs text-slate-700 dark:text-slate-300">{row.name}</span>
                <span class="tabular-nums text-slate-600 dark:text-slate-400">
                  {formatCost(row.cost_usd)}
                  <span class="ml-2 text-xs text-slate-400 dark:text-slate-600">
                    {formatNumber(row.requests)} req
                  </span>
                </span>
              </div>
              <div class="mt-0.5 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                <div
                  class="h-full rounded-full bg-sky-500"
                  style={{ width: `${Math.max(2, (row.cost_usd / max()) * 100)}%` }}
                />
              </div>
            </div>
          )}
        </For>
      </div>
    </Show>
  )
}

export default function InsightsPage() {
  const [timeWindow, setTimeWindow] = createSignal<TimeWindow | null>(null)
  const activeWindow = () => timeWindow() ?? settings().defaultTimeWindow
  const hours = () => WINDOW_HOURS[activeWindow()]

  const [attribution, { refetch: refetchAttribution }] = createResource(activeWindow, (w) =>
    fetchAttribution(WINDOW_HOURS[w]),
  )
  const [latency, { refetch: refetchLatency }] = createResource(activeWindow, (w) =>
    fetchLatency(WINDOW_HOURS[w]),
  )
  const [errors, { refetch: refetchErrors }] = createResource(activeWindow, (w) =>
    fetchErrorStats(WINDOW_HOURS[w]),
  )
  const [tools, { refetch: refetchTools }] = createResource(activeWindow, (w) =>
    fetchToolStats(WINDOW_HOURS[w]),
  )
  const [fleet, { refetch: refetchFleet }] = createResource(fetchFleet)

  createEffect(() => {
    const timer = setInterval(() => {
      refetchAttribution()
      refetchLatency()
      refetchErrors()
      refetchTools()
      refetchFleet()
    }, settings().refreshIntervalMs)
    onCleanup(() => clearInterval(timer))
  })

  const apiError = () => firstError(attribution, latency, errors, tools, fleet)
  const ms = (v: number | null | undefined) => (v == null ? '—' : `${formatNumber(v)} ms`)

  return (
    <div class="min-h-screen">
      <header class="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div class="flex items-center gap-3">
            <a
              href="/"
              class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              ← Dashboard
            </a>
            <h1 class="text-lg font-semibold text-slate-900 dark:text-slate-50">Insights</h1>
          </div>
          <div class="flex items-center gap-3">
            <div class="flex rounded-lg border border-slate-300 overflow-hidden dark:border-slate-700">
              <For each={WINDOW_OPTIONS}>
                {(opt) => (
                  <button
                    type="button"
                    onClick={() => setTimeWindow(opt.value)}
                    class="px-3 py-1 text-xs font-medium transition"
                    classList={{
                      'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900':
                        activeWindow() === opt.value,
                      'bg-white text-slate-600 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-400':
                        activeWindow() !== opt.value,
                    }}
                  >
                    {opt.label}
                  </button>
                )}
              </For>
            </div>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main class="mx-auto max-w-6xl space-y-6 px-6 py-8">
        <p class="text-sm text-slate-500 dark:text-slate-400">
          Everything here comes from telemetry Claude Code has always sent and this service has
          always stored — it just wasn't queryable until the attributes were promoted out of{' '}
          <code class="font-mono text-xs">raw_attributes</code>. Rows read{' '}
          <code class="font-mono text-xs">(none)</code> when the attribute was absent.
        </p>

        <div class="grid gap-6 md:grid-cols-2">
          <Panel title="API latency" hint={`Requests in the last ${hours() === 0 ? 'all time' : `${hours()}h`}`}>
            <Show when={latest(latency)} fallback={<Empty>No requests in this window.</Empty>}>
              {(l) => (
                <div class="grid grid-cols-4 gap-3">
                  <Stat label="p50" value={ms(l().p50_ms)} />
                  <Stat label="p95" value={ms(l().p95_ms)} />
                  <Stat label="max" value={ms(l().max_ms)} />
                  <Stat label="requests" value={formatNumber(l().requests)} />
                </div>
              )}
            </Show>
          </Panel>

          <Panel title="Errors and refusals">
            <Show when={latest(errors)} fallback={<Empty>Nothing recorded.</Empty>}>
              {(e) => (
                <>
                  <div class="grid grid-cols-4 gap-3">
                    <Stat
                      label="error rate"
                      value={`${(e().error_rate * 100).toFixed(1)}%`}
                      accent={e().error_rate > 0.02 ? 'text-red-600 dark:text-red-400' : undefined}
                    />
                    <Stat label="errors" value={formatNumber(e().errors)} />
                    <Stat label="refusals" value={formatNumber(e().refusals)} />
                    <Stat
                      label="retried"
                      value={formatNumber(e().retried)}
                      accent={e().retried > 0 ? 'text-amber-600 dark:text-amber-400' : undefined}
                    />
                  </div>
                  <Show when={e().by_status_code.length > 0}>
                    <div class="mt-3 flex flex-wrap gap-2">
                      <For each={e().by_status_code}>
                        {(s) => (
                          <span class="rounded bg-red-50 px-2 py-0.5 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
                            HTTP {s.status_code || '—'} × {s.n}
                          </span>
                        )}
                      </For>
                    </div>
                  </Show>
                  <Show when={e().by_refusal_category.length > 0}>
                    <div class="mt-2 flex flex-wrap gap-2">
                      <For each={e().by_refusal_category}>
                        {(c) => (
                          <span class="rounded bg-violet-50 px-2 py-0.5 text-xs text-violet-700 dark:bg-violet-950 dark:text-violet-300">
                            refusal: {c.category} × {c.n}
                          </span>
                        )}
                      </For>
                    </div>
                  </Show>
                  <Show when={e().retried > 0}>
                    <p class="mt-3 text-xs text-slate-500 dark:text-slate-400">
                      Retries are also the duplicate-event signal — see the de-duplication note in
                      the data model docs.
                    </p>
                  </Show>
                </>
              )}
            </Show>
          </Panel>

          <Panel
            title="Cost by query source"
            hint="Subagent and auxiliary spend is usually the surprise."
          >
            <CostBars rows={latest(attribution)?.by_query_source} empty="No attribution data." />
          </Panel>

          <Panel title="Cost by effort and speed" hint="Fast mode is priced above standard.">
            <div class="space-y-4">
              <CostBars rows={latest(attribution)?.by_effort} empty="No effort attribute seen." />
              <CostBars rows={latest(attribution)?.by_speed} empty="No speed attribute seen." />
            </div>
          </Panel>

          <Panel title="Cost by agent" hint="Built-in names verbatim; user-defined report as 'custom'.">
            <CostBars rows={latest(attribution)?.by_agent} empty="No agent attribution seen." />
          </Panel>

          <Panel title="Cost by skill and MCP server">
            <div class="space-y-4">
              <CostBars rows={latest(attribution)?.by_skill} empty="No skill attribution seen." />
              <CostBars rows={latest(attribution)?.by_mcp_server} empty="No MCP attribution seen." />
            </div>
          </Panel>
        </div>

        <Panel title="Tool performance" hint="From claude_code.tool_result events.">
          <Show when={(latest(tools) ?? []).length > 0} fallback={<Empty>No tool events yet.</Empty>}>
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="text-left text-xs text-slate-500 dark:text-slate-400">
                    <th class="py-1 font-medium">Tool</th>
                    <th class="py-1 text-right font-medium">Calls</th>
                    <th class="py-1 text-right font-medium">Failures</th>
                    <th class="py-1 text-right font-medium">Avg</th>
                    <th class="py-1 text-right font-medium">Max</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-100 dark:divide-slate-800">
                  <For each={latest(tools) ?? []}>
                    {(row) => (
                      <tr>
                        <td class="py-1.5 font-mono text-xs text-slate-800 dark:text-slate-200">
                          {row.tool_name}
                        </td>
                        <td class="py-1.5 text-right tabular-nums">{formatNumber(row.calls)}</td>
                        <td
                          class="py-1.5 text-right tabular-nums"
                          classList={{ 'text-red-600 dark:text-red-400': row.failures > 0 }}
                        >
                          {formatNumber(row.failures)}
                        </td>
                        <td class="py-1.5 text-right tabular-nums text-slate-500">{ms(row.avg_ms)}</td>
                        <td class="py-1.5 text-right tabular-nums text-slate-500">{ms(row.max_ms)}</td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            </div>
          </Show>
        </Panel>

        <Panel
          title="Fleet"
          hint="Which Claude Code versions and terminals are reporting. All time."
        >
          <Show when={(latest(fleet) ?? []).length > 0} fallback={<Empty>No events yet.</Empty>}>
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="text-left text-xs text-slate-500 dark:text-slate-400">
                    <th class="py-1 font-medium">Version</th>
                    <th class="py-1 font-medium">Terminal</th>
                    <th class="py-1 text-right font-medium">Sessions</th>
                    <th class="py-1 text-left font-medium">Last seen</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-100 dark:divide-slate-800">
                  <For each={latest(fleet) ?? []}>
                    {(row) => (
                      <tr>
                        <td class="py-1.5 font-mono text-xs text-slate-800 dark:text-slate-200">
                          {row.app_version}
                        </td>
                        <td class="py-1.5 text-slate-600 dark:text-slate-400">{row.terminal_type}</td>
                        <td class="py-1.5 text-right tabular-nums">{formatNumber(row.sessions)}</td>
                        <td class="py-1.5 text-slate-500 dark:text-slate-400">
                          {formatTime(row.last_seen_at)}
                        </td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            </div>
          </Show>
        </Panel>
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
