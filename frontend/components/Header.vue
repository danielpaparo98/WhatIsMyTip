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
    </nav>
  </header>
</template>

<script setup lang="ts">
// No state needed - burger menu removed
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
}

@media (min-width: 641px) {
  .header {
    padding: 1.5rem 2rem;
  }
}
</style>
