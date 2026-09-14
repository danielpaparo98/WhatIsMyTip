<template>
  <div v-if="visible" class="off-season-banner">
    <div class="banner-content">
      <div class="banner-emblem">
        <img
          v-if="premierLogo"
          :src="premierLogo"
          :alt="`${premier} logo`"
          class="premier-logo"
          width="48"
          height="48"
        />
        <span v-else class="trophy">*</span>
      </div>
      <div class="banner-text">
        <p class="banner-title">
          <strong>{{ premier }}</strong> &mdash; {{ season }} Premiers
        </p>
        <p class="banner-subtitle">
          Season over &mdash; see you next year!
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import confetti from 'canvas-confetti'
import { getTeamColors } from '~/composables/useTeamColors'
import { useLatestRound } from '~/composables/useLatestRound'

// M-4 (2026-09 review): this component used to fetch
// `/api/games?latest=true` itself on mount.  It now reads the shared
// latest-round store (populated by the index page's useAsyncData and
// refreshed by the page's single poller) — zero extra requests.
const { latestRound } = useLatestRound()
const { getLogoUrl } = useTeamLogos()

const visible = ref(false)
const premier = ref<string | null>(null)
const season = ref<number | null>(null)
const premierLogo = ref('')
const hasFired = ref(false)

/**
 * Fire a single celebratory confetti burst using the premier's colours.
 */
function fireCelebration(colors: string[]) {
  // Left burst
  confetti({
    particleCount: 60,
    spread: 80,
    origin: { x: 0, y: 0.5 },
    colors,
    startVelocity: 30,
    gravity: 0.6,
    scalar: 1.0,
    ticks: 250,
  })
  // Right burst
  confetti({
    particleCount: 60,
    spread: 80,
    origin: { x: 1, y: 0.5 },
    colors,
    startVelocity: 30,
    gravity: 0.6,
    scalar: 1.0,
    ticks: 250,
  })
  // Top centre burst
  setTimeout(() => {
    confetti({
      particleCount: 80,
      spread: 120,
      origin: { x: 0.5, y: 0.1 },
      colors,
      startVelocity: 35,
      gravity: 0.5,
      scalar: 1.1,
      ticks: 300,
    })
  }, 200)
}

// DESIGN-FIX (hydration): the previous `watch(..., { immediate: true })`
// applied state during client SETUP — before hydration patching — while
// the server had rendered the banner-hidden state (the store only
// becomes seeded after the page's payload lands). That produced a
// hydration mismatch on every off-season page load. Apply the initial
// state in onMounted instead; the non-immediate watch handles updates.
watch(latestRound, (data) => {
  if (!data) return
  if (data.is_off_season && data.premier) {
    premier.value = data.premier
    season.value = data.season
    premierLogo.value = getLogoUrl(data.premier)
    visible.value = true

    // Fire confetti once using the premier's colours
    if (!hasFired.value) {
      hasFired.value = true
      const colors = getTeamColors(data.premier)
      fireCelebration(colors)
    }
  } else {
    visible.value = false
  }
})

onMounted(() => {
  // Apply whatever the store already holds (seeded from payload).
  const data = latestRound.value
  if (data?.is_off_season && data.premier) {
    premier.value = data.premier
    season.value = data.season
    premierLogo.value = getLogoUrl(data.premier)
    visible.value = true
    if (!hasFired.value) {
      hasFired.value = true
      fireCelebration(getTeamColors(data.premier))
    }
  }
})
</script>

<style scoped>
.off-season-banner {
  border-bottom: 1px solid var(--color-border);
  background: linear-gradient(135deg, rgba(255, 215, 0, 0.08) 0%, rgba(255, 193, 7, 0.04) 100%);
}

.banner-content {
  max-width: 1400px;
  margin: 0 auto;
  padding: 1.25rem 1.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  flex-wrap: wrap;
}

.banner-emblem {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.premier-logo {
  width: 48px;
  height: 48px;
  object-fit: contain;
}

.trophy {
  font-size: 2rem;
  line-height: 1;
}

.banner-text {
  text-align: center;
}

.banner-title {
  margin: 0;
  font-size: 1rem;
  font-weight: 700;
  color: var(--color-text);
}

.banner-title strong {
  letter-spacing: -0.01em;
}

.banner-subtitle {
  margin: 0.25rem 0 0;
  font-size: 0.875rem;
  color: var(--color-muted);
}

@media (max-width: 640px) {
  .banner-content {
    padding: 1rem;
    flex-direction: column;
    gap: 0.5rem;
  }

  .premier-logo {
    width: 40px;
    height: 40px;
  }

  .banner-title {
    font-size: 0.9375rem;
  }

  .banner-subtitle {
    font-size: 0.8125rem;
  }
}
</style>
