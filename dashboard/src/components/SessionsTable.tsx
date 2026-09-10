import { createEffect, createMemo, createSignal, For, Show } from 'solid-js'
import type { SessionRow } from '../api'
import { updateSessionProject } from '../api'
import { formatCost, formatNumber, formatTime } from '../format'
import { errorMessage } from '../resource'
import { settings } from '../settingsStore'

// One source of truth, from the server. This used to hardcode 15 minutes,
// so the same session could show active here and inactive on /tablet.
function isActive(lastSeenAt: string): boolean {
  return Date.now() - new Date(lastSeenAt).getTime() < settings().activeSessionWindowMin * 60_000
}

// ── Grouped-by-project aggregation ────────────────────────────────────────

interface ProjectGroup {
  project_name: string | null
  session_count: number
  active_count: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
  models: string[]
  last_seen_at: string
}

function buildProjectGroups(sessions: SessionRow[]): ProjectGroup[] {
  const map = new Map<string | null, ProjectGroup>()
  for (const s of sessions) {
    const key = s.project_name ?? null
    if (!map.has(key)) {
      map.set(key, {
        project_name: key,
        session_count: 0,
        active_count: 0,
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: 0,
        models: [],
        last_seen_at: s.last_seen_at,
      })
    }
    const g = map.get(key)!
    g.session_count++
    if (isActive(s.last_seen_at)) g.active_count++
    g.input_tokens += s.input_tokens
    g.output_tokens += s.output_tokens
    g.cost_usd += s.cost_usd
    if (s.models) {
      for (const m of s.models.split(',')) {
        const t = m.trim()
        if (t && !g.models.includes(t)) g.models.push(t)
      }
    }
    if (s.last_seen_at > g.last_seen_at) g.last_seen_at = s.last_seen_at
  }
  return [...map.values()].sort((a, b) => b.cost_usd - a.cost_usd)
}

// ── Grouped table ──────────────────────────────────────────────────────────

function GroupedTable(props: { sessions: SessionRow[] }) {
  const groups = createMemo(() => buildProjectGroups(props.sessions))

  return (
    <div class="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
      <table class="min-w-full divide-y divide-slate-200 text-sm dark:divide-slate-800">
        <thead class="bg-slate-50 dark:bg-slate-900">
          <tr>
            <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Project</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Sessions</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Active</th>
            <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Model(s)</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Input</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Output</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Cost</th>
            <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Last seen</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-200 bg-white dark:divide-slate-800 dark:bg-slate-900">
          <For each={groups()}>
            {(g) => (
              <tr>
                <td class="px-4 py-2 font-medium text-slate-800 dark:text-slate-200">
                  <Show
                    when={g.project_name}
                    fallback={
                      <span class="italic text-slate-400 dark:text-slate-600">(untagged)</span>
                    }
                  >
                    <span class="rounded bg-sky-100 px-2 py-0.5 text-xs text-sky-700 dark:bg-sky-900 dark:text-sky-300">
                      {g.project_name}
                    </span>
                  </Show>
                </td>
                <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                  {g.session_count}
                </td>
                <td class="px-4 py-2 text-right">
                  <Show when={g.active_count > 0}>
                    <span class="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                      <span class="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                      {g.active_count}
                    </span>
                  </Show>
                  <Show when={g.active_count === 0}>
                    <span class="text-slate-400">—</span>
                  </Show>
                </td>
                <td class="px-4 py-2 text-slate-700 dark:text-slate-300 text-xs">
                  {g.models.join(', ') || '—'}
                </td>
                <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                  {formatNumber(g.input_tokens)}
                </td>
                <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                  {formatNumber(g.output_tokens)}
                </td>
                <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                  {formatCost(g.cost_usd)}
                </td>
                <td class="px-4 py-2 text-slate-500 dark:text-slate-400">
                  {formatTime(g.last_seen_at)}
                </td>
              </tr>
            )}
          </For>
        </tbody>
      </table>
      {groups().length === 0 && (
        <p class="p-6 text-center text-sm text-slate-500 dark:text-slate-400">No data yet.</p>
      )}
    </div>
  )
}

// ── Main table (by session) ────────────────────────────────────────────────

export default function SessionsTable(props: {
  sessions: SessionRow[] | undefined
  /** Total on the server; shown when the list is truncated. */
  total?: number
  groupByProject?: boolean
  /** Lets the parent pause polling while a row is being edited. */
  onEditingChange?: (editing: boolean) => void
}) {
  const [projectNames, setProjectNames] = createSignal<Record<string, string | null>>({})
  const [editingId, setEditingId] = createSignal<string | null>(null)
  const [editValue, setEditValue] = createSignal('')
  const [saving, setSaving] = createSignal(false)
  const [saveError, setSaveError] = createSignal<string | null>(null)

  createEffect(() => props.onEditingChange?.(editingId() !== null))

  function startEdit(session: SessionRow) {
    const current = projectNames()[session.session_id] ?? session.project_name
    setEditValue(current ?? '')
    setEditingId(session.session_id)
  }

  async function commitEdit() {
    const id = editingId()
    if (!id) return
    setSaving(true)
    setSaveError(null)
    try {
      const name = editValue().trim() || null
      await updateSessionProject(id, name)
      setProjectNames((prev) => ({ ...prev, [id]: name }))
      setEditingId(null)
    } catch (e) {
      // Stay in edit state so the typed value survives — closing the input on
      // failure looks identical to a successful save and loses the edit.
      setSaveError(errorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  function cancelEdit() {
    setEditingId(null)
    setSaveError(null)
  }

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter') commitEdit()
    if (e.key === 'Escape') cancelEdit()
  }

  const rows = createMemo(() => props.sessions ?? [])

  // Merge optimistic project name overrides into sessions for grouped view
  const mergedSessions = createMemo(() =>
    rows().map((s) => {
      const override = projectNames()[s.session_id]
      return override !== undefined ? { ...s, project_name: override } : s
    }),
  )

  return (
    <Show
      when={!props.groupByProject}
      fallback={<GroupedTable sessions={mergedSessions()} />}
    >
      <Show when={saveError()}>
        {(msg) => (
          <div class="mb-2 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            Couldn't save the project tag: {msg()}
          </div>
        )}
      </Show>
      <div class="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
        <table class="min-w-full divide-y divide-slate-200 text-sm dark:divide-slate-800">
          <thead class="bg-slate-50 dark:bg-slate-900">
            <tr>
              <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Session</th>
              <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Project</th>
              <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">User</th>
              <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Model(s)</th>
              <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Input</th>
              <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Output</th>
              <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Cost</th>
              <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Last seen</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-200 bg-white dark:divide-slate-800 dark:bg-slate-900">
            <For each={rows()}>
              {(session) => {
                const projectName = () => projectNames()[session.session_id] ?? session.project_name
                const isEditing = () => editingId() === session.session_id

                return (
                  <tr>
                    <td class="px-4 py-2 font-mono text-xs text-slate-700 dark:text-slate-300">
                      <span
                        class="mr-2 inline-block h-2 w-2 rounded-full"
                        classList={{
                          'bg-emerald-500': isActive(session.last_seen_at),
                          'bg-slate-400 dark:bg-slate-600': !isActive(session.last_seen_at),
                        }}
                      />
                      {session.session_id.slice(0, 12)}
                    </td>
                    <td class="px-4 py-2">
                      <Show
                        when={isEditing()}
                        fallback={
                          <button
                            type="button"
                            title={
                              session.project_source === 'resource'
                                ? 'Declared by the container via OTEL_RESOURCE_ATTRIBUTES. Tagging here overrides it for this session only.'
                                : session.project_source === 'user_map'
                                  ? 'Inherited from this user ID\'s project mapping.'
                                  : 'Click to tag this session'
                            }
                            onClick={() => startEdit(session)}
                            class="rounded px-1.5 py-0.5 text-xs font-medium transition hover:bg-slate-100 dark:hover:bg-slate-800"
                            classList={{
                              'text-sky-600 dark:text-sky-400': !!projectName(),
                              'text-slate-400 dark:text-slate-600': !projectName(),
                            }}
                          >
                            {projectName() ?? '+ tag'}
                          </button>
                        }
                      >
                        <input
                          ref={(el) =>
                            requestAnimationFrame(() => {
                              if (editingId() === session.session_id) el.focus()
                            })
                          }
                          class="w-32 rounded border border-sky-400 bg-white px-1 py-0.5 text-xs text-slate-900 outline-none dark:bg-slate-800 dark:text-slate-100"
                          value={editValue()}
                          disabled={saving()}
                          onInput={(e) => setEditValue(e.currentTarget.value)}
                          onKeyDown={onKeyDown}
                        />
                      </Show>
                    </td>
                    <td class="px-4 py-2 text-slate-700 dark:text-slate-300">{session.user_id ?? '—'}</td>
                    <td class="px-4 py-2 text-slate-700 dark:text-slate-300">{session.models ?? '—'}</td>
                    <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                      {formatNumber(session.input_tokens)}
                    </td>
                    <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                      {formatNumber(session.output_tokens)}
                    </td>
                    <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                      {formatCost(session.cost_usd)}
                    </td>
                    <td class="px-4 py-2 text-slate-500 dark:text-slate-400">
                      {formatTime(session.last_seen_at)}
                    </td>
                  </tr>
                )
              }}
            </For>
          </tbody>
        </table>
        <Show when={props.total !== undefined && props.total > (props.sessions?.length ?? 0)}>
          <p class="border-t border-slate-200 px-4 py-2 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
            Showing the {props.sessions?.length} most recent of {props.total} sessions. Totals in
            the summary cards cover all of them.
          </p>
        </Show>
        {(!props.sessions || props.sessions.length === 0) && (
          <p class="p-6 text-center text-sm text-slate-500 dark:text-slate-400">
            No sessions yet — once a Claude Code session reports usage, it'll show up here.
          </p>
        )}
      </div>
    </Show>
  )
}
