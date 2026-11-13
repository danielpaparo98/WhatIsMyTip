<template>
  <div class="error-page">
    <div class="error-content">
      <h1>{{ error?.statusCode || 'Error' }}</h1>
      <p>{{ error?.statusMessage || 'Something went wrong' }}</p>
      <NuxtLink :to="homePath" class="back-home">← Back to Home</NuxtLink>
    </div>
  </div>
</template>

<script setup lang="ts">
interface Props {
  error?: {
    statusCode?: number
    statusMessage?: string
  }
}

const props = defineProps<Props>()

// LEAGUE-NAV (2026-09-30, user request): the error page's home link
// follows the active league (defaults to '/' before hydration).
const { homePath } = useActiveLeague()

// ANALYTICS (2026-11-13): client-visible error surface — 404s measure
// broken links, 5xx measure client-visible failures. onMounted only runs
// client-side, so the wrapper's SSR no-op makes prerendered 404 shells
// (which DO execute this script during generate) safe by construction.
onMounted(() => {
  track(ANALYTICS_EVENTS.errorPageShown, {
    status_code: props.error?.statusCode ?? 0,
  })
})
</script>

<style scoped>
.error-page {
  min-height: 60vh;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
}

.error-content h1 {
  font-size: 4rem;
  margin-bottom: 1rem;
}

.error-content p {
  color: var(--color-muted);
  font-size: 1.125rem;
}

.back-home {
  display: inline-block;
  margin-top: 2rem;
  padding: 0.75rem 1.5rem;
  background: var(--color-text);
  color: var(--color-bg);
  text-decoration: none;
  border-radius: 4px;
}

.back-home:hover {
  opacity: 0.8;
}
</style>
