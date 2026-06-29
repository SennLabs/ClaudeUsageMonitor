export function formatNumber(n: number): string {
  return new Intl.NumberFormat('en-US').format(n)
}

export function formatCost(n: number): string {
  return `$${n.toFixed(2)}`
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString()
}
