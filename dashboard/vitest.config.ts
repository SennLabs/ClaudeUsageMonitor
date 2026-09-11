import { defineConfig } from 'vitest/config'

/**
 * Separate from vite.config.ts on purpose.
 *
 * These tests cover pure logic — chart maths, label formatting, number
 * formatting — so they need neither the DOM nor the Solid JSX transform.
 * Running them in the `node` environment keeps the suite dependency-free and
 * fast. A future component test would need `environment: 'jsdom'`, the Solid
 * plugin, and @solidjs/testing-library; add those then, not now.
 */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
