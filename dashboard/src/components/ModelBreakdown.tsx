import { For } from 'solid-js'
import type { ModelUsage } from '../api'
import { formatCost, formatNumber } from '../format'

export default function ModelBreakdown(props: { usage: ModelUsage[] | undefined }) {
  return (
    <div class="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
      <table class="min-w-full divide-y divide-slate-200 text-sm dark:divide-slate-800">
        <thead class="bg-slate-50 dark:bg-slate-900">
          <tr>
            <th class="px-4 py-2 text-left font-medium text-slate-500 dark:text-slate-400">Model</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Requests</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Input</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Output</th>
            <th class="px-4 py-2 text-right font-medium text-slate-500 dark:text-slate-400">Cost</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-200 bg-white dark:divide-slate-800 dark:bg-slate-900">
          <For each={props.usage ?? []}>
            {(row) => (
              <tr>
                <td class="px-4 py-2 text-slate-700 dark:text-slate-300">{row.model}</td>
                <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                  {formatNumber(row.requests)}
                </td>
                <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                  {formatNumber(row.input_tokens)}
                </td>
                <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">
                  {formatNumber(row.output_tokens)}
                </td>
                <td class="px-4 py-2 text-right text-slate-700 dark:text-slate-300">{formatCost(row.cost_usd)}</td>
              </tr>
            )}
          </For>
        </tbody>
      </table>
      {(!props.usage || props.usage.length === 0) && (
        <p class="p-6 text-center text-sm text-slate-500 dark:text-slate-400">No usage recorded yet.</p>
      )}
    </div>
  )
}
