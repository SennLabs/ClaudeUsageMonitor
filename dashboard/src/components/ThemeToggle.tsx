import { createSignal, onMount } from 'solid-js'

export default function ThemeToggle() {
  const [isDark, setIsDark] = createSignal(true)

  onMount(() => {
    setIsDark(document.documentElement.classList.contains('dark'))
  })

  function toggle() {
    const next = !isDark()
    setIsDark(next)
    document.documentElement.classList.toggle('dark', next)
    try {
      localStorage.setItem('theme', next ? 'dark' : 'light')
    } catch {
      // Site data blocked: the toggle still works for this page view.
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-200 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
    >
      {isDark() ? '☀️ Light' : '🌙 Dark'}
    </button>
  )
}
