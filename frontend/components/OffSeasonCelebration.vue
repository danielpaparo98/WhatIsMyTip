<template>
  <section class="celebration">
    <span class="eyebrow">Season {{ season ?? '' }} Complete</span>

    <template v-if="premier">
      <img
        :src="getLogoUrl(premier)"
        :alt="`${premier} logo`"
        class="premier-logo"
        loading="lazy"
        decoding="async"
        width="160"
        height="160"
      />
      <h1 class="premier-name">{{ getTeamDisplayName(premier) }}</h1>
      <p class="wordmark">Premiers</p>
      <p class="message">
        Congratulations to the {{ getTeamDisplayName(premier) }}. The {{ season }} season is in
        the books &mdash; tips return next season.
      </p>
    </template>

    <template v-else>
      <h1 class="premier-name">Off Season</h1>
      <p class="wordmark">See You Next Year</p>
      <p class="message">
        The season is in the books &mdash; tips return next season.
      </p>
    </template>
  </section>
</template>

<script setup lang="ts">
import confetti from 'canvas-confetti'
import { getTeamColors } from '~/composables/useTeamColors'

interface Props {
  premier: string | null
  season: number | null
}

const props = defineProps<Props>()

const { getLogoUrl, getTeamDisplayName } = useTeamLogos()

/**
 * One celebratory burst in the premier's colours on mount.
 *
 * ConfettiEffect reacts to `is_grand_final` (pre-match week, gold
 * palette) and stays silent post-season, so the celebration page fires
 * its own single burst — client-only in onMounted, mirroring
 * OffSeasonBanner, so SSR/prerendered HTML never diverges from the
 * hydrated DOM.
 */
function celebrate() {
  if (!props.premier) return
  const colors = getTeamColors(props.premier)
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

onMounted(celebrate)
</script>

<style scoped>
.celebration {
  min-height: calc(100vh - 200px);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 3.5rem 1.5rem;
  gap: 0.75rem;
}

.eyebrow {
  font-size: 0.75rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  border: 1px solid var(--color-text);
  padding: 0.375rem 0.875rem;
  margin-bottom: 1rem;
}

.premier-logo {
  width: 120px;
  height: 120px;
  object-fit: contain;
  margin-bottom: 0.5rem;
}

.premier-name {
  font-size: clamp(2.5rem, 9vw, 6rem);
  line-height: 1.05;
  margin: 0;
}

.wordmark {
  font-size: clamp(1.125rem, 3vw, 1.75rem);
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.2em;
  margin: 0 0 1rem;
}

.message {
  font-size: 1.125rem;
  max-width: 560px;
  margin: 0;
}

/* Mobile */
@media (max-width: 640px) {
  .celebration {
    padding: 2.5rem 1rem;
  }

  .premier-logo {
    width: 96px;
    height: 96px;
  }
}

/* Desktop */
@media (min-width: 1025px) {
  .premier-logo {
    width: 160px;
    height: 160px;
  }

  .message {
    font-size: 1.25rem;
  }
}
</style>
