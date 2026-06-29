import { createResource, createSignal, For, onMount, Show } from 'solid-js'
import { fetchBackupStatus, fetchUsageByModel, triggerBackup } from '../api'
import type { BackupStatus } from '../api'
import {
  type AppSettings,
  type Metric,
  type ModelPrice,
  type TimeWindow,
  loadSettings,
  saveSettings,
} from '../settings'

function Section(props: { title: string; description?: string; children: any }) {
  return (
    <div class="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <h2 class="text-base font-semibold text-slate-900 dark:text-slate-50">{props.title}</h2>
      <Show when={props.description}>
        <p class="mt-1 text-sm text-slate-500 dark:text-slate-400">{props.description}</p>
      </Show>
      <div class="mt-4 space-y-4">{props.children}</div>
    </div>
  )
}

function Field(props: { label: string; hint?: string; children: any }) {
  return (
    <div class="flex items-start justify-between gap-6">
      <div class="min-w-0">
        <p class="text-sm font-medium text-slate-700 dark:text-slate-300">{props.label}</p>
        <Show when={props.hint}>
          <p class="text-xs text-slate-500 dark:text-slate-500">{props.hint}</p>
        </Show>
      </div>
      <div class="flex-shrink-0">{props.children}</div>
    </div>
  )
}

function OptionGroup<T extends string>(props: {
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div class="flex rounded-lg border border-slate-300 overflow-hidden dark:border-slate-600">
      {props.options.map((opt) => (
        <button
          type="button"
          onClick={() => props.onChange(opt.value)}
          class="px-3 py-1.5 text-sm font-medium transition"
          classList={{
            'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900':
              props.value === opt.value,
            'bg-white text-slate-600 hover:bg-slate-50 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-slate-700':
              props.value !== opt.value,
          }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

const inputClass =
  'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100'

export default function SettingsPage() {
  const [usageByModel] = createResource(fetchUsageByModel)
  const [backupStatus, { refetch: refetchBackup }] = createResource(fetchBackupStatus)
  const [triggerError, setTriggerError] = createSignal<string | null>(null)
  const [triggering, setTriggering] = createSignal(false)
  const [saved, setSaved] = createSignal(false)

  async function handleTriggerBackup() {
    setTriggering(true)
    setTriggerError(null)
    try {
      await triggerBackup()
      await refetchBackup()
    } catch (e) {
      setTriggerError((e as Error).message)
    } finally {
      setTriggering(false)
    }
  }

  // ── form state ─────────────────────────────────────────────────────────
  const [monthlyBudget, setMonthlyBudget] = createSignal('')
  const [billingDay, setBillingDay] = createSignal(1)
  const [refreshMs, setRefreshMs] = createSignal(5000)
  const [activeWindowMin, setActiveWindowMin] = createSignal(15)
  const [defaultTimeWindow, setDefaultTimeWindow] = createSignal<TimeWindow>('24h')
  const [defaultMetric, setDefaultMetric] = createSignal<Metric>('cost')
  const [alertThreshold, setAlertThreshold] = createSignal('')
  const [modelPrices, setModelPrices] = createSignal<Record<string, ModelPrice>>({})
  const [newModelName, setNewModelName] = createSignal('')

  onMount(() => {
    const s = loadSettings()
    setMonthlyBudget(s.monthlyBudget !== null ? String(s.monthlyBudget) : '')
    setBillingDay(s.billingCycleDay)
    setRefreshMs(s.refreshIntervalMs)
    setActiveWindowMin(s.activeSessionWindowMin)
    setDefaultTimeWindow(s.defaultTimeWindow)
    setDefaultMetric(s.defaultMetric)
    setAlertThreshold(
      s.costAlertThresholdPerHour !== null ? String(s.costAlertThresholdPerHour) : '',
    )
    setModelPrices({ ...s.modelPrices })
  })

  function handleSave() {
    const budget = parseFloat(monthlyBudget())
    const alert = parseFloat(alertThreshold())
    const settings: AppSettings = {
      monthlyBudget: !isNaN(budget) && budget > 0 ? budget : null,
      billingCycleDay: billingDay(),
      refreshIntervalMs: refreshMs(),
      activeSessionWindowMin: activeWindowMin(),
      defaultTimeWindow: defaultTimeWindow(),
      defaultMetric: defaultMetric(),
      costAlertThresholdPerHour: !isNaN(alert) && alert > 0 ? alert : null,
      modelPrices: modelPrices(),
    }
    saveSettings(settings)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  function setPrice(model: string, field: keyof ModelPrice, raw: string) {
    const v = parseFloat(raw)
    if (isNaN(v)) return
    setModelPrices((prev) => ({
      ...prev,
      [model]: { ...(prev[model] ?? { input_per_mtok: 0, output_per_mtok: 0 }), [field]: v },
    }))
  }

  function addModel() {
    const name = newModelName().trim()
    if (!name || modelPrices()[name]) return
    setModelPrices((prev) => ({ ...prev, [name]: { input_per_mtok: 0, output_per_mtok: 0 } }))
    setNewModelName('')
  }

  function removeModel(model: string) {
    setModelPrices((prev) => {
      const next = { ...prev }
      delete next[model]
      return next
    })
  }

  const allModels = () => {
    const fromUsage = (usageByModel() ?? []).map((m) => m.model)
    const fromOverrides = Object.keys(modelPrices())
    return [...new Set([...fromUsage, ...fromOverrides])].sort()
  }

  return (
    <div class="min-h-screen bg-slate-50 dark:bg-slate-950">
      <header class="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div class="mx-auto flex max-w-3xl items-center gap-4 px-6 py-4">
          <a
            href="/"
            class="text-sm text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200 transition"
          >
            ← Dashboard
          </a>
          <h1 class="text-lg font-semibold text-slate-900 dark:text-slate-50">Settings</h1>
        </div>
      </header>

      <main class="mx-auto max-w-3xl space-y-6 px-6 py-8">

        {/* ── Usage & Budget ─────────────────────────────────── */}
        <Section title="Usage & Budget" description="Track spend against your Anthropic plan limits.">
          <Field label="Monthly budget" hint="Leave blank to disable the progress bar.">
            <div class="flex items-center gap-1">
              <span class="text-sm text-slate-500">$</span>
              <input
                class={`w-28 ${inputClass}`}
                type="number"
                min="0"
                step="1"
                placeholder="e.g. 100"
                value={monthlyBudget()}
                onInput={(e) => setMonthlyBudget(e.currentTarget.value)}
              />
            </div>
          </Field>

          <Field
            label="Billing cycle resets on day"
            hint="Day of the month your Anthropic billing period starts."
          >
            <input
              class={`w-20 ${inputClass}`}
              type="number"
              min="1"
              max="28"
              value={billingDay()}
              onInput={(e) => setBillingDay(parseInt(e.currentTarget.value) || 1)}
            />
          </Field>
        </Section>

        {/* ── Chart defaults ─────────────────────────────────── */}
        <Section
          title="Chart Defaults"
          description="Starting state for the chart each time the dashboard loads."
        >
          <Field label="Default time window">
            <OptionGroup
              value={defaultTimeWindow()}
              options={[
                { value: '24h', label: '24 h' },
                { value: '7d', label: '7 d' },
                { value: '30d', label: '30 d' },
              ]}
              onChange={setDefaultTimeWindow}
            />
          </Field>

          <Field label="Default metric">
            <OptionGroup
              value={defaultMetric()}
              options={[
                { value: 'cost', label: 'Cost' },
                { value: 'tokens', label: 'Tokens' },
              ]}
              onChange={setDefaultMetric}
            />
          </Field>
        </Section>

        {/* ── Monitoring / alerts ────────────────────────────── */}
        <Section
          title="Monitoring"
          description="Trigger a visual warning when live spend rate exceeds a threshold."
        >
          <Field
            label="Hourly spend alert"
            hint="A banner appears when the current rate exceeds this. Leave blank to disable."
          >
            <div class="flex items-center gap-1">
              <span class="text-sm text-slate-500">$</span>
              <input
                class={`w-28 ${inputClass}`}
                type="number"
                min="0"
                step="0.01"
                placeholder="e.g. 5.00"
                value={alertThreshold()}
                onInput={(e) => setAlertThreshold(e.currentTarget.value)}
              />
              <span class="text-sm text-slate-500">/ hr</span>
            </div>
          </Field>
        </Section>

        {/* ── Display ────────────────────────────────────────── */}
        <Section title="Display">
          <Field label="Auto-refresh interval" hint="How often the dashboard polls for new data.">
            <select
              class={inputClass}
              value={refreshMs()}
              onChange={(e) => setRefreshMs(parseInt(e.currentTarget.value))}
            >
              <option value={5000}>5 seconds</option>
              <option value={10000}>10 seconds</option>
              <option value={30000}>30 seconds</option>
              <option value={60000}>1 minute</option>
            </select>
          </Field>

          <Field
            label="Active session window"
            hint="A session is considered active if it had activity within this window."
          >
            <div class="flex items-center gap-1">
              <input
                class={`w-20 ${inputClass}`}
                type="number"
                min="1"
                max="120"
                value={activeWindowMin()}
                onInput={(e) => setActiveWindowMin(parseInt(e.currentTarget.value) || 15)}
              />
              <span class="text-sm text-slate-500">min</span>
            </div>
          </Field>
        </Section>

        {/* ── Model price overrides ──────────────────────────── */}
        <Section
          title="Model Price Overrides"
          description="Override the cost_usd reported by Claude Code with your own prices. Useful if you're on a custom pricing tier or enterprise agreement. Prices are in USD per 1 million tokens."
        >
          <Show when={allModels().length > 0}>
            <div class="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
              <table class="min-w-full text-sm">
                <thead class="bg-slate-50 dark:bg-slate-800">
                  <tr>
                    <th class="px-3 py-2 text-left font-medium text-slate-500 dark:text-slate-400">
                      Model
                    </th>
                    <th class="px-3 py-2 text-right font-medium text-slate-500 dark:text-slate-400">
                      Input ($/MTok)
                    </th>
                    <th class="px-3 py-2 text-right font-medium text-slate-500 dark:text-slate-400">
                      Output ($/MTok)
                    </th>
                    <th class="px-3 py-2" />
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-200 bg-white dark:divide-slate-700 dark:bg-slate-900">
                  <For each={allModels()}>
                    {(model) => (
                      <tr>
                        <td class="px-3 py-2 font-mono text-xs text-slate-700 dark:text-slate-300">
                          {model}
                        </td>
                        <td class="px-3 py-2 text-right">
                          <input
                            class={`w-24 text-right ${inputClass}`}
                            type="number"
                            min="0"
                            step="0.01"
                            placeholder="—"
                            value={modelPrices()[model]?.input_per_mtok ?? ''}
                            onInput={(e) => setPrice(model, 'input_per_mtok', e.currentTarget.value)}
                          />
                        </td>
                        <td class="px-3 py-2 text-right">
                          <input
                            class={`w-24 text-right ${inputClass}`}
                            type="number"
                            min="0"
                            step="0.01"
                            placeholder="—"
                            value={modelPrices()[model]?.output_per_mtok ?? ''}
                            onInput={(e) =>
                              setPrice(model, 'output_per_mtok', e.currentTarget.value)
                            }
                          />
                        </td>
                        <td class="px-3 py-2 text-right">
                          <Show when={modelPrices()[model]}>
                            <button
                              type="button"
                              onClick={() => removeModel(model)}
                              class="text-xs text-slate-400 hover:text-red-500 transition"
                            >
                              ✕
                            </button>
                          </Show>
                        </td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            </div>
          </Show>

          <div class="flex items-center gap-2">
            <input
              class={`flex-1 ${inputClass}`}
              placeholder="Add model (e.g. claude-opus-4-8)"
              value={newModelName()}
              onInput={(e) => setNewModelName(e.currentTarget.value)}
              onKeyDown={(e) => e.key === 'Enter' && addModel()}
            />
            <button
              type="button"
              onClick={addModel}
              class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 transition"
            >
              Add
            </button>
          </div>

          <p class="text-xs text-slate-400 dark:text-slate-600">
            When a price override exists for a model, the Model Breakdown table shows an adjusted
            cost column alongside the reported cost.
          </p>
        </Section>

        {/* ── Backup ─────────────────────────────────────────── */}
        <BackupSection
          status={backupStatus()}
          triggering={triggering()}
          triggerError={triggerError()}
          onTrigger={handleTriggerBackup}
        />

        {/* ── Other ideas ────────────────────────────────────── */}
        <Section title="Other settings to consider adding">
          <ul class="space-y-2 text-sm text-slate-500 dark:text-slate-400 list-disc list-inside">
            <li>
              <strong class="text-slate-700 dark:text-slate-300">Organisation / dashboard name</strong>
              {' '}— displayed on the tablet view header
            </li>
            <li>
              <strong class="text-slate-700 dark:text-slate-300">Dark mode preference</strong>
              {' '}— persist the toggle choice instead of per-component
            </li>
            <li>
              <strong class="text-slate-700 dark:text-slate-300">Webhook URL</strong>
              {' '}— send a POST when spend crosses a threshold (requires backend support)
            </li>
            <li>
              <strong class="text-slate-700 dark:text-slate-300">Data retention</strong>
              {' '}— prune events older than N days to keep the SQLite file small
            </li>
          </ul>
        </Section>

        {/* ── Save ───────────────────────────────────────────── */}
        <div class="flex items-center gap-3">
          <button
            type="button"
            onClick={handleSave}
            class="rounded-lg bg-sky-600 px-5 py-2 text-sm font-semibold text-white hover:bg-sky-700 transition"
          >
            Save settings
          </button>
          <Show when={saved()}>
            <span class="text-sm text-emerald-600 dark:text-emerald-400">Saved ✓</span>
          </Show>
        </div>
      </main>
    </div>
  )
}

function fmtDateTime(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

function BackupSection(props: {
  status: BackupStatus | undefined
  triggering: boolean
  triggerError: string | null
  onTrigger: () => void
}) {
  return (
    <Section
      title="Database Backup"
      description="Periodically copy the SQLite database to a NAS or remote server. Configure via environment variables on the ingest container — see docker-compose.yml for examples."
    >
      <Show
        when={props.status}
        fallback={<p class="text-sm text-slate-500">Loading backup status…</p>}
      >
        {(s) => (
          <>
            <Show
              when={s().enabled}
              fallback={
                <div class="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400">
                  <p class="font-medium text-slate-700 dark:text-slate-300 mb-1">Backup is not configured</p>
                  <p>
                    Set <code class="rounded bg-slate-200 px-1 dark:bg-slate-700">BACKUP_DESTINATION</code> on
                    the ingest container to enable. Use a local path (NAS mount) or an rsync SSH target like{' '}
                    <code class="rounded bg-slate-200 px-1 dark:bg-slate-700">user@host:/path/to/backups/</code>.
                  </p>
                  <p class="mt-2">Optional variables:</p>
                  <ul class="ml-4 mt-1 list-disc space-y-0.5">
                    <li><code class="rounded bg-slate-200 px-1 dark:bg-slate-700">BACKUP_INTERVAL_HOURS</code> — how often to run (default: 24)</li>
                    <li><code class="rounded bg-slate-200 px-1 dark:bg-slate-700">BACKUP_KEEP</code> — how many copies to keep locally (default: 7)</li>
                    <li><code class="rounded bg-slate-200 px-1 dark:bg-slate-700">BACKUP_SSH_KEY</code> — path to SSH private key for rsync</li>
                  </ul>
                </div>
              }
            >
              <div class="space-y-3">
                {/* Status grid */}
                <div class="grid grid-cols-2 gap-3 text-sm">
                  <StatusRow label="Destination" value={s().destination ?? '—'} mono />
                  <StatusRow
                    label="Method"
                    value={s().method === 'rsync-ssh' ? 'rsync over SSH' : 'local copy'}
                  />
                  <StatusRow label="Schedule" value={`every ${s().interval_hours}h · keep ${s().keep}`} />
                  <StatusRow label="Next backup" value={fmtDateTime(s().next_backup_at)} />
                  <StatusRow label="Last backup" value={fmtDateTime(s().last_backup_at)} />
                  <StatusRow
                    label="Last result"
                    value={
                      s().last_backup_ok === null
                        ? 'Never run'
                        : s().last_backup_ok
                          ? 'OK ✓'
                          : 'Failed ✗'
                    }
                    accent={
                      s().last_backup_ok === null
                        ? undefined
                        : s().last_backup_ok
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : 'text-red-600 dark:text-red-400'
                    }
                  />
                </div>

                <Show when={s().last_backup_error}>
                  <div class="rounded-lg border border-red-200 bg-red-50 p-3 text-xs font-mono text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-400">
                    {s().last_backup_error}
                  </div>
                </Show>

                <div class="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={props.onTrigger}
                    disabled={props.triggering}
                    class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 transition"
                  >
                    {props.triggering ? 'Running…' : 'Run backup now'}
                  </button>
                  <Show when={props.triggerError}>
                    <span class="text-sm text-red-600 dark:text-red-400">{props.triggerError}</span>
                  </Show>
                </div>
              </div>
            </Show>
          </>
        )}
      </Show>
    </Section>
  )
}

function StatusRow(props: { label: string; value: string; mono?: boolean; accent?: string }) {
  return (
    <>
      <div class="text-slate-500 dark:text-slate-400">{props.label}</div>
      <div
        class={`font-medium ${props.accent ?? 'text-slate-800 dark:text-slate-200'} ${props.mono ? 'font-mono text-xs break-all' : ''}`}
      >
        {props.value}
      </div>
    </>
  )
}
