import { For } from 'solid-js'
import type { SessionRow } from '../api'
import { formatCost, formatNumber, formatTime } from '../format'

const ACTIVE_WINDOW_MS = 15 * 60 * 1000

function isActive(lastSeenAt: string): boolean {
  return Date.now() - new Date(lastSeenAt).getTime() < ACTIVE_WINDOW_MS
}

export default function SessionsTable(props: { sessions: SessionRow[] | undefined }) {
  return (
    <div class="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
      <table class="min-w-full divide-y divide-slate-200 text-sm dark:divide-slate-800">
        <thead class="bg-slate-50 dark:bg-slate-900">
          <tr>
            <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Session</th>
            <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">User</th>
            <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Model(s)</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Input</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Output</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Cost</th>
            <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Last seen</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-200 bg-white dark:divide-slate-800 dark:bg-slate-900">
          <For each={props.sessions ?? []}>
            {(session) => (
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
                <td class="px-4 py-2 text-slate-500 dark:text-slate-400">{formatTime(session.last_seen_at)}</td>
              </tr>
            )}
          </For>
        </tbody>
      </table>
      {(!props.sessions || props.sessions.length === 0) && (
        <p class="p-6 text-center text-sm text-slate-500 dark:text-slate-400">
          No sessions yet — once a Claude Code session reports usage, it'll show up here.
        </p>
      )}
    </div>
  )
}
