import type { Summary } from '../api'
import { formatCost, formatNumber } from '../format'

const CARDS: Array<{ label: string; key: keyof Summary; format: (n: number) => string }> = [
  { label: 'Active sessions', key: 'active_sessions', format: formatNumber },
  { label: 'Total sessions', key: 'total_sessions', format: formatNumber },
  { label: 'Input tokens', key: 'total_input_tokens', format: formatNumber },
  { label: 'Output tokens', key: 'total_output_tokens', format: formatNumber },
  { label: 'Cache read tokens', key: 'total_cache_read_tokens', format: formatNumber },
  { label: 'Total cost', key: 'total_cost_usd', format: formatCost },
]

export default function SummaryCards(props: { summary: Summary | undefined }) {
  return (
    <div class="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
      {CARDS.map((card) => (
        <div class="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <p class="text-xs font-medium tracking-wide text-slate-500 uppercase dark:text-slate-400">
            {card.label}
          </p>
          <p class="mt-1 text-2xl font-semibold text-slate-900 dark:text-slate-50">
            {props.summary ? card.format(props.summary[card.key]) : '—'}
          </p>
        </div>
      ))}
    </div>
  )
}
