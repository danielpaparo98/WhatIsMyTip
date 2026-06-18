<template>
  <div class="app">
    <NuxtLayout>
      <NuxtPage />
    </NuxtLayout>
  </div>
</template>

<script setup lang="ts">
/**
 * FX-10: Global error boundary.
 * Catches unhandled errors from any descendant component and routes them
 * through Nuxt's error page via `showError`.  Also logs to the console in
 * dev so they remain visible during local development.
 */
onErrorCaptured((error, _instance, info) => {
  if (import.meta.dev) {
    // eslint-disable-next-line no-console
    

    // FX-13: dev-only logging so prod bundles stay quiet.
 (import.meta.dev) console.error('[app.vue] onErrorCaptured:', error, info)
  }
  // Defer to Nuxt's built-in error overlay / page.
  showError({
    statusCode: 500,
    statusMessage: error instanceof Error ? error.message : 'Unexpected error',
    fatal: true,
  })
  // Tell Vue we've handled the error so it does not bubble further.
  return false
})
</script>

<style>
.app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
</style>
