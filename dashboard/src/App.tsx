import { createResource, onCleanup, onMount } from 'solid-js'
import { fetchSessions, fetchSummary, fetchUsageByModel } from './api'
import ModelBreakdown from './components/ModelBreakdown'
import SessionsTable from './components/SessionsTable'
import SummaryCards from './components/SummaryCards'
import ThemeToggle from './components/ThemeToggle'

const REFRESH_INTERVAL_MS = 5000

export default function App() {
  const [summary, { refetch: refetchSummary }] = createResource(fetchSummary)
  const [sessions, { refetch: refetchSessions }] = createResource(fetchSessions)
  const [usageByModel, { refetch: refetchUsage }] = createResource(fetchUsageByModel)

  let timer: ReturnType<typeof setInterval>

  onMount(() => {
    timer = setInterval(() => {
      refetchSummary()
      refetchSessions()
      refetchUsage()
    }, REFRESH_INTERVAL_MS)
  })

  onCleanup(() => clearInterval(timer))

  return (
    <div class="min-h-screen">
      <header class="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <h1 class="text-lg font-semibold text-slate-900 dark:text-slate-50">Claude Usage Monitor</h1>
          <ThemeToggle />
        </div>
      </header>

      <main class="mx-auto max-w-6xl space-y-8 px-6 py-8">
        <section>
          <SummaryCards summary={summary()} />
        </section>

        <section>
          <h2 class="mb-3 text-sm font-semibold tracking-wide text-slate-500 uppercase dark:text-slate-400">
            Sessions
          </h2>
          <SessionsTable sessions={sessions()} />
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
