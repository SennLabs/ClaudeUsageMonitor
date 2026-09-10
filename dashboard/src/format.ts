export function formatNumber(n: number): string {
  return new Intl.NumberFormat('en-US').format(n)
}

export function formatCost(n: number): string {
  return `$${n.toFixed(2)}`
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString()
}

/**
 * Compact token counts for large type. `(n / 1000).toFixed(0)` printed "0k" as
 * a 5xl headline for anything under 500, and rounded 1,500 to "2k".
 */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 10_000) return `${Math.round(n / 1000)}k`
  if (n >= 1_000) return `${(n / 1000).toFixed(1)}k`
  return formatNumber(n)
}
