<template>
  <!-- Invisible trigger — confetti fires programmatically -->
</template>

<script setup lang="ts">
import confetti from 'canvas-confetti'
import { GRAND_FINAL_COLORS } from '~/composables/useTeamColors'
import { useLatestRound } from '~/composables/useLatestRound'

// GF-DESIGN (2026-09-20, user request): the burst uses the TWO GRAND
// FINALISTS' kit colours instead of a generic gold palette.  The home
// page passes them in from the resolved GF game; when unknown we fall
// back to the celebratory golds.
const props = withDefaults(
  defineProps<{ colors?: string[] }>(),
  { colors: () => GRAND_FINAL_COLORS },
)

// M-4 (2026-09 review): this component used to fetch
// `/api/games?latest=true` AND run its own 5-minute poller.  It now
// reacts to the shared latest-round store (populated by the index
// page's useAsyncData and refreshed by the page's single poller).
//
// GF-DESIGN: mounted from index.vue ONLY (was layouts/default.vue) so
// confetti never fires on other pages (user request).
const { latestRound } = useLatestRound()

let animationTimer: ReturnType<typeof setInterval> | null = null
let active = false

/**
 * Fire a single confetti burst from a random edge origin.
 */
function fireBurst() {
  const origins: Array<{ x: number; y: number }> = [
    { x: 0, y: 0.2 },
    { x: 0, y: 0.6 },
    { x: 1, y: 0.2 },
    { x: 1, y: 0.6 },
    { x: 0.5, y: 0.1 },
  ]
  const origin = origins[Math.floor(Math.random() * origins.length)]
  confetti({
    particleCount: 40 + Math.floor(Math.random() * 40),
    spread: 70 + Math.floor(Math.random() * 50),
    origin,
    colors: props.colors,
    startVelocity: 25 + Math.floor(Math.random() * 15),
    gravity: 0.7,
    scalar: 0.9,
    ticks: 200,
  })
}

function stop() {
  active = false
  if (animationTimer) {
    clearInterval(animationTimer)
    animationTimer = null
  }
}

watch(
  latestRound,
  (data) => {
    // PRERENDER FIX: mounted from index.vue AFTER the GF data resolves,
    // so the immediate watch now fires on the SERVER during prerender
    // (previously the layout mounted it before the data landed, so the
    // immediate call saw null).  canvas-confetti touches `document`,
    // which does not exist server-side — bursts are client-only.
    if (import.meta.server) return
    if (!data) return
    if (data.is_grand_final && !active) {
      active = true
      // Fire immediately on activation
      fireBurst()
      // Then every 3–5 seconds
      animationTimer = setInterval(fireBurst, 3000 + Math.random() * 2000)
    } else if (!data.is_grand_final && active) {
      stop()
    }
  },
  { immediate: true },
)

onUnmounted(stop)
</script>
