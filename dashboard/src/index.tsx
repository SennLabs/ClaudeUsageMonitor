/* @refresh reload */
import { ErrorBoundary } from 'solid-js'
import { render } from 'solid-js/web'
import { Router, Route } from '@solidjs/router'
import './index.css'
import App from './App.tsx'
import TabletDashboard from './components/TabletDashboard.tsx'
import SettingsPage from './components/SettingsPage.tsx'
import UsersPage from './components/UsersPage.tsx'
import InsightsPage from './components/InsightsPage.tsx'

const root = document.getElementById('root')

/**
 * Backstop only. Every known throw source is handled at the call site (see
 * resource.ts); this exists so an unhandled one shows a message instead of a
 * blank page.
 */
function Fallback(err: unknown, reset: () => void) {
  return (
    <div class="flex min-h-screen items-center justify-center p-6">
      <div class="max-w-lg rounded-xl border border-red-200 bg-red-50 p-6 dark:border-red-900 dark:bg-red-950">
        <h1 class="text-base font-semibold text-red-800 dark:text-red-300">
          Something broke in the dashboard
        </h1>
        <pre class="mt-2 overflow-x-auto text-xs whitespace-pre-wrap text-red-700 dark:text-red-400">
          {err instanceof Error ? err.message : String(err)}
        </pre>
        <button
          type="button"
          onClick={reset}
          class="mt-4 rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white"
        >
          Retry
        </button>
      </div>
    </div>
  )
}

render(
  () => (
    <ErrorBoundary fallback={Fallback}>
      <Router>
        <Route path="/" component={App} />
        <Route path="/tablet" component={TabletDashboard} />
        <Route path="/settings" component={SettingsPage} />
        <Route path="/users" component={UsersPage} />
        <Route path="/insights" component={InsightsPage} />
      </Router>
    </ErrorBoundary>
  ),
  root!,
)
