const STORAGE_KEY = 'color-mode'

export const useColorMode = () => {
  // DESIGN-FIX (2026-09): the previous implementation read localStorage
  // inside the useState initializer. useState's initializer only runs
  // when the key is absent — the SSR payload always ships 'light', so on
  // hydration the initializer never ran, onMounted then STRIPPED the
  // `.dark` class the inline <head> script had correctly applied, and
  // dark mode was broken for every user on every load.
  // Resolve the real preference in onMounted instead.
  const colorMode = useState<'light' | 'dark'>(STORAGE_KEY, () => 'light')

  const isDark = computed(() => colorMode.value === 'dark')

  function toggle() {
    colorMode.value = isDark.value ? 'light' : 'dark'
  }

  onMounted(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    const resolved: 'light' | 'dark' =
      stored === 'dark' || stored === 'light'
        ? stored
        : window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light'

    // Sync the DOM class directly (the watcher only fires on CHANGE, so
    // an unchanged resolved value would leave the class stripped).
    document.documentElement.classList.toggle('dark', resolved === 'dark')
    colorMode.value = resolved
  })

  watch(colorMode, (mode) => {
    if (import.meta.client) {
      document.documentElement.classList.toggle('dark', mode === 'dark')
      localStorage.setItem(STORAGE_KEY, mode)
    }
  })

  return { colorMode, isDark, toggle }
}
