<template>
  <div class="league-selector">
    <select
      class="league-select"
      :value="activeLeague"
      aria-label="Select league"
      title="League"
      @change="onSelect"
    >
      <option v-for="league in LEAGUES" :key="league.key" :value="league.key">
        {{ league.shortLabel }}
      </option>
    </select>
    <svg class="chevron" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9" /></svg>
  </div>
</template>

<script setup lang="ts">
// League list is STATIC for now — dynamic discovery via /api/sports
// lands with the read-side cutover (P4-1 follow-up).
import { LEAGUES, useActiveLeague } from '~/composables/useSportConfig'

const { activeLeague, setActiveLeague } = useActiveLeague()

function onSelect(event: Event) {
  setActiveLeague((event.target as HTMLSelectElement).value)
}
</script>

<style scoped>
.league-selector {
  position: relative;
  display: inline-flex;
  align-items: center;
}

/* Mirrors the header nav items (uppercase/bold/tracked) and the
   theme-toggle's 2px border treatment. */
.league-select {
  appearance: none;
  background: none;
  border: 2px solid var(--color-border);
  color: var(--color-text);
  cursor: pointer;
  font-weight: 700;
  text-transform: uppercase;
  font-size: 0.875rem;
  letter-spacing: 0.05em;
  padding: 0.5rem 1.75rem 0.5rem 0.75rem;
  min-width: 44px;
  min-height: 44px;
  transition: border-color 0.2s ease;
}

.league-select:hover {
  border-color: var(--color-text);
}

.league-select:focus-visible {
  outline: 2px solid var(--color-text);
  outline-offset: 2px;
}

.chevron {
  position: absolute;
  right: 0.5rem;
  pointer-events: none;
}

/* Mobile styles */
@media (max-width: 640px) {
  .league-select {
    font-size: 0.75rem;
    padding: 0.375rem 1.5rem 0.375rem 0.5rem;
    min-width: 36px;
    min-height: 36px;
  }

  .chevron {
    width: 12px;
    height: 12px;
  }
}
</style>
