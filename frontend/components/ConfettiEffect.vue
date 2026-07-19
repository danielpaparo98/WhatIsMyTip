<template>
  <!-- Invisible trigger — confetti fires programmatically -->
</template>

<script setup lang="ts">
import confetti from 'canvas-confetti'
import { GRAND_FINAL_COLORS } from '~/composables/useTeamColors'

const api = useApi()

const AUTO_REFRESH_MS = 5 * 60 * 1000
let animationTimer: ReturnType<typeof setInterval> | null = null
let refreshTimer: ReturnType<typeof setInterval> | null = null
let active = false

/**
 * Fire a single confetti burst from a random edge origin.
 * Uses gold/celebratory colours since the winner isn't known
 * during the grand-final round itself.
 */
function fireBurst() {
  const colors = GRAND_FINAL_COLORS
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
    colors,
    startVelocity: 25 + Math.floor(Math.random() * 15),
    gravity: 0.7,
    scalar: 0.9,
    ticks: 200,
  })
}

async function checkAndStart() {
  try {
    const data = await api.getLatestRound()
    if (data.is_grand_final && !active) {
      active = true
      // Fire immediately on activation
      fireBurst()
      // Then every 3–5 seconds
      animationTimer = setInterval(fireBurst, 3000 + Math.random() * 2000)
    } else if (!data.is_grand_final && active) {
      stop()
    }
  } catch {
    // Silently ignore — show stops on errors
    if (active) stop()
  }
}

function stop() {
  active = false
  if (animationTimer) {
    clearInterval(animationTimer)
    animationTimer = null
  }
}

onMounted(() => {
  checkAndStart()
  // Periodically recheck (in case the round changes or the page was open
  // when the grand-final round started)
  refreshTimer = setInterval(checkAndStart, AUTO_REFRESH_MS)
})

onUnmounted(() => {
  stop()
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})
</script>
