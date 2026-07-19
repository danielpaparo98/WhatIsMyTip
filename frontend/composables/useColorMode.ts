const STORAGE_KEY = 'color-mode'

export const useColorMode = () => {
  const colorMode = useState<'light' | 'dark'>(STORAGE_KEY, () => {
    // During SSR / initial render, read from localStorage if available
    // (the inline <head> script already handles the class, but we need
    // the correct initial value for reactive state).
    if (import.meta.client) {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored === 'dark' || stored === 'light') return stored
      // No stored preference — fall back to system preference
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    }
    return 'light'
  })

  const isDark = computed(() => colorMode.value === 'dark')

  function toggle() {
    colorMode.value = isDark.value ? 'light' : 'dark'
  }

  onMounted(() => {
    // Ensure the <html> class matches the resolved state on first paint.
    // The inline <head> script already covers the initial load, but
    // during client-side navigation the DOM class may not be in sync.
    document.documentElement.classList.toggle('dark', colorMode.value === 'dark')
  })

  watch(colorMode, (mode) => {
    if (import.meta.client) {
      document.documentElement.classList.toggle('dark', mode === 'dark')
      localStorage.setItem(STORAGE_KEY, mode)
    }
  })

  return { colorMode, isDark, toggle }
}
