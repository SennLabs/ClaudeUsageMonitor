import { createResource, Show } from 'solid-js'
import { fetchVersion } from '../api'

/**
 * The dashboard's own version, baked in at build time, plus the ingest
 * service's. They ship as separate containers, so a half-finished upgrade can
 * leave them out of step; that is flagged rather than hidden.
 */
export default function VersionTag() {
  const [ingest] = createResource(() => fetchVersion().catch(() => null))
  const mismatch = () => {
    const v = ingest()?.version
    return v !== undefined && v !== __APP_VERSION__ ? v : null
  }

  return (
    <span
      class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs font-normal text-slate-500 dark:bg-slate-800 dark:text-slate-400"
      title={`Dashboard v${__APP_VERSION__}${ingest()?.version ? ` · ingest v${ingest()!.version}` : ''}`}
    >
      v{__APP_VERSION__}
      <Show when={mismatch()}>
        {(v) => (
          <span class="ml-1 text-amber-600 dark:text-amber-400">(ingest v{v()})</span>
        )}
      </Show>
    </span>
  )
}
