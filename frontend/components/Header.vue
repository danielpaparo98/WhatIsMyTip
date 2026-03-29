<template>
  <header class="header">
    <nav class="nav">
      <NuxtLink to="/" class="logo">
        <h1>WhatIsMyTip<span class="dot">.</span>com</h1>
      </NuxtLink>
      <ul class="nav-links">
        <li><NuxtLink to="/">Tips</NuxtLink></li>
        <li><NuxtLink to="/backtest">Backtest</NuxtLink></li>
        <li><NuxtLink to="/about">About</NuxtLink></li>
      </ul>
    </nav>
  </header>
</template>

<style scoped>
.header {
  border-bottom: 1px solid var(--color-border);
  padding: 1.5rem 2rem;
}

.nav {
  display: flex;
  justify-content: space-between;
  align-items: center;
  max-width: 1400px;
  margin: 0 auto;
}

.logo h1 {
  font-size: 1.5rem;
  margin: 0;
  font-weight: 800;
}

.logo .dot {
  color: var(--color-muted);
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
}

.nav-links a.router-link-active {
  text-decoration: none;
}
</style>
