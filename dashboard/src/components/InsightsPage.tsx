import { createEffect, createResource, createSignal, For, onCleanup, Show } from 'solid-js'
import type { AttributionRow } from '../api'
import {
  exportCsvUrl,
  fetchAttribution,
  fetchAudit,
  fetchCacheEfficiency,
  fetchErrorStats,
  fetchFleet,
  fetchLatency,
  fetchPrompts,
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
  const [cache, { refetch: refetchCache }] = createResource(activeWindow, (w) =>
    fetchCacheEfficiency(WINDOW_HOURS[w]),
  )
  const [prompts, { refetch: refetchPrompts }] = createResource(activeWindow, (w) =>
    fetchPrompts(WINDOW_HOURS[w]),
  )
  const [audit, { refetch: refetchAudit }] = createResource(activeWindow, (w) =>
    fetchAudit(WINDOW_HOURS[w]),
  )

  createEffect(() => {
    const timer = setInterval(() => {
      refetchAttribution()
      refetchLatency()
      refetchErrors()
      refetchTools()
      refetchFleet()
      refetchCache()
      refetchPrompts()
      refetchAudit()
    }, settings().refreshIntervalMs)
    onCleanup(() => clearInterval(timer))
  })

  const apiError = () =>
    firstError(attribution, latency, errors, tools, fleet, cache, prompts, audit)
  const ms = (v: number | null | undefined) => (v == null ? '—' : `${formatNumber(v)} ms`)
  const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)

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
            <a
              href={exportCsvUrl()}
              class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              Export CSV
            </a>
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

        <Show when={(latest(audit)?.bypass_count ?? 0) > 0}>
          <div class="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
            <strong>{latest(audit)!.bypass_count}</strong> session(s) entered{' '}
            <code class="font-mono text-xs">bypassPermissions</code> in this window. See the audit
            panel below.
          </div>
        </Show>

        <Panel
          title="Cache efficiency"
          hint="Cached input is billed at a fraction of uncached. One of the few levers that actually moves the bill."
        >
          <Show when={latest(cache)} fallback={<Empty>No requests in this window.</Empty>}>
            {(c) => (
              <>
                <div class="grid grid-cols-4 gap-3">
                  <Stat
                    label="hit ratio"
                    value={pct(c().overall.hit_ratio)}
                    accent={
                      c().overall.hit_ratio !== null && c().overall.hit_ratio! < 0.5
                        ? 'text-amber-600 dark:text-amber-400'
                        : 'text-emerald-600 dark:text-emerald-400'
                    }
                  />
                  <Stat label="from cache" value={formatNumber(c().overall.cache_read_tokens)} />
                  <Stat label="uncached" value={formatNumber(c().overall.uncached_input_tokens)} />
                  <Stat label="cache writes" value={formatNumber(c().overall.cache_creation_tokens)} />
                </div>
                <Show when={c().by_project.length > 0}>
                  <table class="mt-4 w-full text-sm">
                    <thead>
                      <tr class="text-left text-xs text-slate-500 dark:text-slate-400">
                        <th class="py-1 font-medium">Project</th>
                        <th class="py-1 text-right font-medium">Hit ratio</th>
                        <th class="py-1 text-right font-medium">From cache</th>
                        <th class="py-1 text-right font-medium">Uncached</th>
                        <th class="py-1 text-right font-medium">Cost</th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-100 dark:divide-slate-800">
                      <For each={c().by_project}>
                        {(row) => (
                          <tr>
                            <td class="py-1.5 text-slate-800 dark:text-slate-200">{row.name}</td>
                            <td class="py-1.5 text-right tabular-nums">{pct(row.hit_ratio)}</td>
                            <td class="py-1.5 text-right tabular-nums text-slate-500">
                              {formatNumber(row.cache_read_tokens)}
                            </td>
                            <td class="py-1.5 text-right tabular-nums text-slate-500">
                              {formatNumber(row.uncached_input_tokens)}
                            </td>
                            <td class="py-1.5 text-right tabular-nums">{formatCost(row.cost_usd)}</td>
                          </tr>
                        )}
                      </For>
                    </tbody>
                  </table>
                </Show>
              </>
            )}
          </Show>
        </Panel>

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
        <Panel
          title="Most expensive prompts"
          hint="Every event of one user prompt shares a prompt.id, so this is what a single question cost."
        >
          <Show when={(latest(prompts) ?? []).length > 0} fallback={<Empty>No prompt IDs in this window.</Empty>}>
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="text-left text-xs text-slate-500 dark:text-slate-400">
                    <th class="py-1 font-medium">Started</th>
                    <th class="py-1 font-medium">Project</th>
                    <th class="py-1 text-right font-medium">Requests</th>
                    <th class="py-1 text-right font-medium">Tokens</th>
                    <th class="py-1 text-right font-medium">Cost</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-100 dark:divide-slate-800">
                  <For each={latest(prompts) ?? []}>
                    {(row) => (
                      <tr title={row.prompt_id}>
                        <td class="py-1.5 text-slate-500 dark:text-slate-400">
                          {formatTime(row.started_at)}
                        </td>
                        <td class="py-1.5 text-slate-800 dark:text-slate-200">{row.project_name}</td>
                        <td class="py-1.5 text-right tabular-nums">{formatNumber(row.requests)}</td>
                        <td class="py-1.5 text-right tabular-nums text-slate-500">
                          {formatNumber(row.total_tokens)}
                        </td>
                        <td class="py-1.5 text-right tabular-nums">{formatCost(row.cost_usd)}</td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            </div>
          </Show>
        </Panel>

        <Panel
          title="Audit"
          hint="Permission-mode changes, failed logins and MCP connectivity — nothing to do with cost."
        >
          <Show
            when={
              (latest(audit)?.permission_changes.length ?? 0) > 0 ||
              (latest(audit)?.auth_failures.length ?? 0) > 0 ||
              (latest(audit)?.mcp_connections.length ?? 0) > 0
            }
            fallback={<Empty>No audit events in this window.</Empty>}
          >
            <div class="space-y-4">
              <Show when={(latest(audit)?.permission_changes.length ?? 0) > 0}>
                <div>
                  <p class="text-xs font-medium text-slate-500 dark:text-slate-400">
                    Permission mode changes
                  </p>
                  <div class="mt-1 space-y-1">
                    <For each={latest(audit)!.permission_changes.slice(0, 10)}>
                      {(c) => (
                        <div
                          class="flex items-baseline justify-between text-sm"
                          classList={{
                            'text-amber-700 dark:text-amber-400': c.to_mode === 'bypassPermissions',
                            'text-slate-700 dark:text-slate-300': c.to_mode !== 'bypassPermissions',
                          }}
                        >
                          <span class="font-mono text-xs">
                            {c.from_mode} → {c.to_mode}
                            <span class="ml-2 text-slate-400 dark:text-slate-600">{c.trigger}</span>
                          </span>
                          <span class="text-xs text-slate-500">{formatTime(c.occurred_at)}</span>
                        </div>
                      )}
                    </For>
                  </div>
                </div>
              </Show>
              <Show when={(latest(audit)?.auth_failures.length ?? 0) > 0}>
                <div>
                  <p class="text-xs font-medium text-slate-500 dark:text-slate-400">Failed logins</p>
                  <div class="mt-1 space-y-1">
                    <For each={latest(audit)!.auth_failures.slice(0, 10)}>
                      {(a) => (
                        <div class="flex items-baseline justify-between text-sm text-red-700 dark:text-red-400">
                          <span class="font-mono text-xs">
                            {a.action} — {a.error_category ?? 'unknown'}
                          </span>
                          <span class="text-xs text-slate-500">{formatTime(a.occurred_at)}</span>
                        </div>
                      )}
                    </For>
                  </div>
                </div>
              </Show>
              <Show when={(latest(audit)?.mcp_connections.length ?? 0) > 0}>
                <div>
                  <p class="text-xs font-medium text-slate-500 dark:text-slate-400">MCP servers</p>
                  <div class="mt-1 flex flex-wrap gap-2">
                    <For each={latest(audit)!.mcp_connections}>
                      {(m) => (
                        <span
                          class="rounded px-2 py-0.5 text-xs"
                          classList={{
                            'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300':
                              m.status === 'failed',
                            'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400':
                              m.status !== 'failed',
                          }}
                        >
                          {m.server} · {m.status} · {m.transport} × {m.n}
                        </span>
                      )}
                    </For>
                  </div>
                </div>
              </Show>
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
