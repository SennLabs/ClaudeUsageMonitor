import { defineConfig } from 'vite'
import solid from 'vite-plugin-solid'
import tailwindcss from '@tailwindcss/vite'
import { readFileSync } from 'node:fs'

// The repo-root VERSION file is the one source of truth for both services.
const version = readFileSync(new URL('../VERSION', import.meta.url), 'utf8').trim()

export default defineConfig({
  plugins: [solid(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(version),
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
