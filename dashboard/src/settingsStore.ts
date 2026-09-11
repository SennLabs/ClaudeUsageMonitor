import { createResource, createRoot } from 'solid-js'
import type { AppSettings } from './api'
import { fetchSettings } from './api'
import { latest } from './resource'

/**
 * Settings are instance-wide and live on the server, so every view shares one
 * resource. A save on /settings is visible everywhere on the next read without
 * a reload — previously each component called loadSettings() once at mount
 * against its own browser's localStorage, so the wall tablet and a laptop held
 * different values and neither noticed a change until refreshed.
 *
 * Theme is deliberately NOT here: a wall display and a laptop reasonably differ,
 * so it stays in localStorage (see ThemeToggle).
 */
const store = createRoot(() => {
  const [settings, { refetch, mutate }] = createResource(fetchSettings)
  return { settings, refetch, mutate }
})

export const settingsResource = store.settings
export const refetchSettings = store.refetch
export const mutateSettings = store.mutate

/** Used until the first fetch resolves, and if the API is unreachable. */
export const FALLBACK_SETTINGS: AppSettings = {
  monthlyBudget: null,
  billingCycleDay: 1,
  refreshIntervalMs: 5000,
  defaultTimeWindow: '24h',
  defaultMetric: 'cost',
  costAlertThresholdPerHour: null,
  displayTimeZone: 'UTC',
  activeSessionWindowMin: 15,
}

/** Reactive read. Never throws and never returns undefined. */
export function settings(): AppSettings {
  return latest(settingsResource) ?? FALLBACK_SETTINGS
}
