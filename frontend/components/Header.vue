<template>
  <header class="header">
    <nav class="nav">
      <NuxtLink to="/" class="logo" aria-label="WhatIsMyTip home">
        <span class="logo-text">WhatIsMyTip</span>
      </NuxtLink>
      <ul class="nav-links">
        <li><NuxtLink to="/">Tips</NuxtLink></li>
        <li><NuxtLink to="/backtest">Backtest</NuxtLink></li>
        <li><NuxtLink to="/about">About</NuxtLink></li>
      </ul>
      <button @click="toggle" class="theme-toggle" :aria-label="isDark ? 'Switch to light mode' : 'Switch to dark mode'" :title="isDark ? 'Light mode' : 'Dark mode'">
        <svg v-if="isDark" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
        <svg v-else xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
      </button>
    </nav>
  </header>
</template>

<script setup lang="ts">
const { isDark, toggle } = useColorMode()
</script>

<style scoped>
.header {
  border-bottom: 1px solid var(--color-border);
  padding: 1rem 1.5rem;
  position: sticky;
  top: 0;
  background: var(--color-bg);
  z-index: 50;
}

.nav {
  display: flex;
  justify-content: space-between;
  align-items: center;
  max-width: 1400px;
  margin: 0 auto;
}

.logo {
  font-weight: 800;
  letter-spacing: -0.02em;
}

.logo-text {
  font-size: 1.25rem;
}

.nav-links {
  display: flex;
  gap: 2rem;
  list-style: none;
  margin: 0;
  padding: 0;
}

.nav-links a {
  font-weight: 700;
  text-transform: uppercase;
  font-size: 0.875rem;
  letter-spacing: 0.05em;
  padding: 0.5rem;
  transition: color 0.2s ease;
}

.nav-links a:hover {
  color: var(--color-muted);
}

.nav-links a.router-link-active {
  text-decoration: none;
}

.theme-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: 2px solid var(--color-border);
  color: var(--color-text);
  cursor: pointer;
  padding: 0.5rem;
  min-width: 44px;
  min-height: 44px;
  transition: border-color 0.2s ease;
}

.theme-toggle:hover {
  border-color: var(--color-text);
}

/* Mobile styles */
@media (max-width: 640px) {
  .header {
    padding: 1rem;
  }

  .nav {
    flex-direction: column;
    gap: 1rem;
    text-align: center;
  }

  .logo-text {
    font-size: 1.125rem;
  }

  .nav-links {
    gap: 1rem;
  }

  .nav-links a {
    font-size: 0.75rem;
    padding: 0.25rem 0.5rem;
  }

  .theme-toggle {
    align-self: center;
  }
}

@media (min-width: 641px) {
  .header {
    padding: 1.5rem 2rem;
  }
}
</style>
