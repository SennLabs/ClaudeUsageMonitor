import { createMemo, createResource, createSignal, For, onCleanup, onMount, Show } from 'solid-js'
import type { UserRow } from '../api'
import { fetchProjects, fetchUsers, setUserProject } from '../api'
import { formatCost, formatNumber, formatTime } from '../format'
import { errorMessage, firstError, latest } from '../resource'
import { loadSettings } from '../settings'
import ThemeToggle from './ThemeToggle'

function relative(iso: string | null) {
  if (!iso) return 'never'
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export default function UsersPage() {
  const settings = loadSettings()

  const [users, { refetch: refetchUsers }] = createResource(fetchUsers)
  const [projects, { refetch: refetchProjects }] = createResource(fetchProjects)

  const [editingId, setEditingId] = createSignal<string | null>(null)
  const [editValue, setEditValue] = createSignal('')
  const [saving, setSaving] = createSignal(false)
  const [notice, setNotice] = createSignal<string | null>(null)
  const [error, setError] = createSignal<string | null>(null)

  let timer: ReturnType<typeof setInterval>
  onMount(() => {
    timer = setInterval(() => {
      // Don't yank the field out from under an in-progress edit
      if (editingId() === null) {
        refetchUsers()
        refetchProjects()
      }
    }, settings.refreshIntervalMs)
  })
  onCleanup(() => clearInterval(timer))

  function startEdit(user: UserRow) {
    setEditValue(user.project_name ?? '')
    setEditingId(user.user_id)
    setError(null)
  }

  async function commitEdit() {
    const id = editingId()
    if (!id) return
    setSaving(true)
    setError(null)
    try {
      const name = editValue().trim()
      const result = await setUserProject(id, name || null)
      setNotice(
        result.project_name
          ? `Linked ${id} to "${result.project_name}" — ${result.sessions_updated} session(s) relabelled.`
          : `Unlinked ${id}. Its sessions are untagged again.`,
      )
      setEditingId(null)
      await Promise.all([refetchUsers(), refetchProjects()])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter') commitEdit()
    if (e.key === 'Escape') setEditingId(null)
  }

  const rows = createMemo(() => latest(users) ?? [])
  const linkedCount = createMemo(() => rows().filter((u) => u.project_name).length)

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
            <h1 class="text-lg font-semibold text-slate-900 dark:text-slate-50">Users &amp; projects</h1>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main class="mx-auto max-w-6xl space-y-6 px-6 py-8">
        <div class="rounded-xl border border-amber-300 bg-amber-50 p-5 dark:border-amber-800 dark:bg-amber-950">
          <h2 class="text-base font-semibold text-amber-900 dark:text-amber-200">
            Prefer declaring the project on the container
          </h2>
          <p class="mt-1.5 max-w-3xl text-sm text-amber-800 dark:text-amber-300">
            Claude Code generates <code class="font-mono text-xs">user.id</code> per{' '}
            <em>installation</em> and stores it in{' '}
            <code class="font-mono text-xs">~/.claude.json</code>. If a dev container's home
            directory does not persist across rebuilds — the usual case — every rebuild produces a
            new ID and silently orphans the mapping below.
          </p>
          <p class="mt-2 max-w-3xl text-sm text-amber-800 dark:text-amber-300">
            Add this to the container's Claude Code settings instead. It survives rebuilds, needs
            no mapping, and is correct on the container's very first event:
          </p>
          <pre class="mt-2 overflow-x-auto rounded-lg bg-amber-100 p-3 text-xs text-amber-900 dark:bg-amber-900/40 dark:text-amber-100">{'"OTEL_RESOURCE_ATTRIBUTES": "project=my-project"'}</pre>
          <p class="mt-2 text-sm text-amber-800 dark:text-amber-300">
            Sessions labelled that way show <strong>resource</strong> as their source and are not
            affected by anything on this page.
          </p>
        </div>

        <div class="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
          <h2 class="text-base font-semibold text-slate-900 dark:text-slate-50">
            Fallback: link a user ID to a project
          </h2>
          <p class="mt-1.5 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            For containers you cannot reconfigure, and for fixing historical data. Link a{' '}
            <code class="font-mono text-xs">user.id</code> once and every session it owns — past
            and future — is tagged with that project.
          </p>
          <ul class="mt-3 space-y-1 text-sm text-slate-500 dark:text-slate-400">
            <li>• Setting a link relabels that user's existing sessions immediately.</li>
            <li>• New sessions from that user arrive already tagged.</li>
            <li>
              • A project set by hand on an individual session wins — a link never overwrites it
              afterwards.
            </li>
            <li>
              • A project declared by the container also wins, and is left alone by this page.
            </li>
            <li>• Clearing the field removes the link and untags that user's sessions.</li>
          </ul>
        </div>

        <Show when={notice()}>
          {(msg) => (
            <div class="flex items-start justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">
              <span>{msg()}</span>
              <button type="button" onClick={() => setNotice(null)} class="font-medium">
                ✕
              </button>
            </div>
          )}
        </Show>

        <Show when={error()}>
          {(msg) => (
            <div class="rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
              Couldn't save: {msg()}
            </div>
          )}
        </Show>

        <div>
          <div class="mb-3 flex items-center justify-between">
            <h2 class="text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
              Users
            </h2>
            <span class="text-xs text-slate-500 dark:text-slate-400">
              {linkedCount()} of {rows().length} linked
            </span>
          </div>

          <div class="overflow-x-auto rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
            <table class="w-full text-sm">
              <thead class="bg-slate-50 dark:bg-slate-900">
                <tr>
                  <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">User ID</th>
                  <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Project</th>
                  <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Sessions</th>
                  <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Active</th>
                  <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Input</th>
                  <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Output</th>
                  <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Cost</th>
                  <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Last seen</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100 dark:divide-slate-800">
                <For each={rows()}>
                  {(user) => (
                    <tr class="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                      <td class="px-4 py-2 font-mono text-xs text-slate-900 dark:text-slate-100">
                        <span title={user.user_id}>{user.user_id}</span>
                        <Show when={user.organization_id}>
                          <span class="ml-2 text-slate-400 dark:text-slate-600">
                            {user.organization_id}
                          </span>
                        </Show>
                      </td>
                      <td class="px-4 py-2">
                        <Show
                          when={editingId() === user.user_id}
                          fallback={
                            <button
                              type="button"
                              title="Click to link this user to a project"
                              onClick={() => startEdit(user)}
                              class="rounded px-1.5 py-0.5 text-xs font-medium transition hover:bg-slate-100 dark:hover:bg-slate-800"
                              classList={{
                                'text-sky-600 dark:text-sky-400': !!user.project_name,
                                'text-slate-400 dark:text-slate-600': !user.project_name,
                              }}
                            >
                              {user.project_name ?? '+ link'}
                            </button>
                          }
                        >
                          <div class="flex items-center gap-1">
                            <input
                              list="known-projects"
                              ref={(el) =>
                                requestAnimationFrame(() => {
                                  if (editingId() === user.user_id) el.focus()
                                })
                              }
                              class="w-40 rounded border border-sky-400 bg-white px-1 py-0.5 text-xs text-slate-900 outline-none dark:bg-slate-800 dark:text-slate-100"
                              value={editValue()}
                              disabled={saving()}
                              placeholder="project name"
                              onInput={(e) => setEditValue(e.currentTarget.value)}
                              onKeyDown={onKeyDown}
                            />
                            <button
                              type="button"
                              onClick={commitEdit}
                              disabled={saving()}
                              class="rounded bg-sky-600 px-2 py-0.5 text-xs font-medium text-white disabled:opacity-50"
                            >
                              {saving() ? '…' : 'Save'}
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditingId(null)}
                              class="px-1 text-xs text-slate-500 dark:text-slate-400"
                            >
                              ✕
                            </button>
                          </div>
                        </Show>
                      </td>
                      <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                        {formatNumber(user.session_count)}
                      </td>
                      <td class="px-4 py-2 text-right">
                        <Show
                          when={user.active_sessions > 0}
                          fallback={<span class="text-slate-400 dark:text-slate-600">—</span>}
                        >
                          <span class="inline-flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400">
                            <span class="h-2 w-2 rounded-full bg-emerald-500" />
                            {user.active_sessions}
                          </span>
                        </Show>
                      </td>
                      <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                        {formatNumber(user.input_tokens)}
                      </td>
                      <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                        {formatNumber(user.output_tokens)}
                      </td>
                      <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                        {formatCost(user.cost_usd)}
                      </td>
                      <td class="px-4 py-2 text-slate-500 dark:text-slate-400">
                        <span title={user.last_seen_at ? formatTime(user.last_seen_at) : undefined}>
                          {relative(user.last_seen_at)}
                        </span>
                      </td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>

            <Show when={rows().length === 0}>
              <p class="p-6 text-center text-sm text-slate-500 dark:text-slate-400">
                No users yet — once a Claude Code session reports usage, its user ID shows up here.
              </p>
            </Show>
          </div>

          {/* Autocomplete source for the project field */}
          <datalist id="known-projects">
            <For each={latest(projects) ?? []}>{(name) => <option value={name} />}</For>
          </datalist>
        </div>
      </main>

      <Show when={firstError(users, projects)}>
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
