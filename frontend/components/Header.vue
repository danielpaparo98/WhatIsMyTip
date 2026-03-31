<template>
  <header class="header">
    <nav class="nav">
      <NuxtLink to="/" class="logo" aria-label="WhatIsMyTip home">
        <span class="logo-text">WhatIsMyTip<span class="dot">.</span>com</span>
      </NuxtLink>
      <button
        class="mobile-menu-btn"
        @click="toggleMenu"
        :aria-expanded="isMenuOpen"
        aria-label="Toggle navigation menu"
      >
        <span class="hamburger"></span>
      </button>
      <ul class="nav-links" :class="{ 'is-open': isMenuOpen }">
        <li><NuxtLink to="/" @click="closeMenu">Tips</NuxtLink></li>
        <li><NuxtLink to="/backtest" @click="closeMenu">Backtest</NuxtLink></li>
        <li><NuxtLink to="/about" @click="closeMenu">About</NuxtLink></li>
      </ul>
    </nav>
  </header>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const isMenuOpen = ref(false)

const toggleMenu = () => {
  isMenuOpen.value = !isMenuOpen.value
}

const closeMenu = () => {
  isMenuOpen.value = false
}
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

.logo h1 {
  font-size: 1.25rem;
  margin: 0;
  font-weight: 800;
}

.logo .dot {
  color: var(--color-muted);
}

.mobile-menu-btn {
  display: none;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  width: 44px;
  height: 44px;
  padding: 0;
  background: transparent;
  border: 2px solid var(--color-border);
  border-radius: 0.5rem;
  cursor: pointer;
  transition: all 0.2s ease;
}

.mobile-menu-btn:hover {
  border-color: var(--color-text);
  background: var(--color-hover);
}

.hamburger {
  position: relative;
  width: 20px;
  height: 2px;
  background: var(--color-text);
  transition: all 0.3s ease;
}

.hamburger::before,
.hamburger::after {
  content: '';
  position: absolute;
  width: 20px;
  height: 2px;
  background: var(--color-text);
  transition: all 0.3s ease;
}

.hamburger::before {
  transform: translateY(-6px);
}

.hamburger::after {
  transform: translateY(6px);
}

.mobile-menu-btn[aria-expanded="true"] .hamburger {
  background: transparent;
}

.mobile-menu-btn[aria-expanded="true"] .hamburger::before {
  transform: rotate(45deg);
}

.mobile-menu-btn[aria-expanded="true"] .hamburger::after {
  transform: rotate(-45deg);
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
@media (max-width: 768px) {
  .header {
    padding: 0.75rem 1rem;
  }

  .logo h1 {
    font-size: 1rem;
  }

  .mobile-menu-btn {
    display: flex;
  }

  .nav-links {
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    flex-direction: column;
    gap: 0;
    background: var(--color-bg);
    border-bottom: 1px solid var(--color-border);
    padding: 0;
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.3s ease;
  }

  .nav-links.is-open {
    max-height: 300px;
  }

  .nav-links li {
    border-bottom: 1px solid var(--color-border);
  }

  .nav-links li:last-child {
    border-bottom: none;
  }

  .nav-links a {
    display: block;
    padding: 1rem 1.5rem;
    font-size: 1rem;
  }
}

@media (min-width: 769px) {
  .header {
    padding: 1.5rem 2rem;
  }

  .logo h1 {
    font-size: 1.5rem;
  }
}
</style>
