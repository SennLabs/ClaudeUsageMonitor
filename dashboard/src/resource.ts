import type { Resource } from 'solid-js'

/**
 * Read a resource without throwing.
 *
 * Solid throws the stored error when you call an errored resource's accessor.
 * In this app that throw happens inside a render effect created *before* the
 * error banner's, so it aborts the update batch and the banner is never
 * inserted — a failed API call renders a blank page with no message at all.
 *
 * Reading `.error` first avoids the throw, and `.latest` keeps the last good
 * value on screen during a failed refetch instead of blanking the view.
 */
export function latest<T>(resource: Resource<T>): T | undefined {
  return resource.error ? undefined : resource.latest
}

/** The first resource error, for a single shared "backend is down" banner. */
export function firstError(...resources: Resource<unknown>[]): unknown {
  for (const resource of resources) {
    if (resource.error) return resource.error
  }
  return undefined
}

/** Human-readable text for whatever a rejected fetch threw. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}
