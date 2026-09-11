import { describe, expect, it } from 'vitest'
import { formatCost, formatNumber, formatTokens } from './format'

describe('formatTokens', () => {
  // These thresholds exist because of a specific regression: the headline on
  // /tablet is 5xl type, and `(n / 1000).toFixed(0)` rendered anything under
  // 500 as a giant "0k" and rounded 1,500 to "2k".
  it('does not collapse small counts to 0k', () => {
    expect(formatTokens(0)).toBe('0')
    expect(formatTokens(1)).toBe('1')
    expect(formatTokens(499)).toBe('499')
    expect(formatTokens(999)).toBe('999')
  })

  it('keeps one decimal between 1k and 10k, where rounding loses the most', () => {
    expect(formatTokens(1_000)).toBe('1.0k')
    expect(formatTokens(1_500)).toBe('1.5k')
    expect(formatTokens(9_949)).toBe('9.9k')
  })

  it('drops to whole thousands once the decimal stops mattering', () => {
    expect(formatTokens(10_000)).toBe('10k')
    expect(formatTokens(10_500)).toBe('11k')
    expect(formatTokens(999_499)).toBe('999k')
  })

  it('switches to millions at a million', () => {
    expect(formatTokens(1_000_000)).toBe('1.0M')
    expect(formatTokens(2_450_000)).toBe('2.5M')
  })
})

describe('formatCost / formatNumber', () => {
  it('always shows cents', () => {
    expect(formatCost(0)).toBe('$0.00')
    expect(formatCost(1)).toBe('$1.00')
    expect(formatCost(12.345)).toBe('$12.35')
  })

  it('groups thousands', () => {
    expect(formatNumber(1_904_322)).toBe('1,904,322')
    expect(formatNumber(0)).toBe('0')
  })
})
