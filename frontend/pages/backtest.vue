<script setup lang="ts">
// PERF-RENAME (2026-10-07): /backtest moved permanently to /performance.
//
// PRIMARY mechanism: the nitro routeRules 301 in nuxt.config.ts.
// Under the static preset the prerender request for /backtest resolves
// through that rule to an h3 sendRedirect response (301 + a zero-delay
// meta-refresh body), which nitropack's prerenderer treats as a VALID
// route and writes to backtest/index.html — so static hosting still
// redirects legacy /backtest links.
//
// THIS stub is the fallback: with my sandbox tooling the old page file
// could not be deleted (no shell), so it was reduced to this redirect
// instead of leaving a 1200-line duplicate AFL page at /backtest. It
// also keeps client-side router navigations to /backtest working if
// the routeRule is ever removed. Orchestrator: `git rm` this file (and
// drop the routeRule + the '/backtest' prerender entry with it) if a
// file-less redirect is preferred — the static-host behaviour would
// then fall back to the host's 404 for legacy links.
await navigateTo('/performance', { redirectCode: 301 })
</script>

<template>
  <p>
    This page moved. Redirecting to
    <NuxtLink to="/performance">Performance</NuxtLink>…
  </p>
</template>
