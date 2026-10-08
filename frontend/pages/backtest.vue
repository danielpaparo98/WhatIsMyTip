<script setup lang="ts">
// PERF-RENAME (2026-10-07): /backtest moved permanently to /performance.
//
// TWO complementary mechanisms (review-consistent rationale):
//
// 1. The nitro routeRules 301 in nuxt.config.ts is the HTTP artifact:
//    under the static preset the prerender request for /backtest
//    resolves through that rule to an h3 sendRedirect response (301 +
//    a zero-delay meta-refresh body), which nitropack's prerenderer
//    treats as a VALID route and writes to backtest/index.html — so
//    static hosting still redirects legacy /backtest links.
//
// 2. THIS stub is the SPA-navigation fallback: a routeRule only fires
//    on HTTP requests, NOT on client-side vue-router navigations (an
//    in-app <NuxtLink to="/backtest"> or router.push from a stale
//    bundle would otherwise render a 404). This page handles those
//    navigations with a real 301 redirect.
await navigateTo('/performance', { redirectCode: 301 })
</script>

<template>
  <p>
    This page moved. Redirecting to
    <NuxtLink to="/performance">Performance</NuxtLink>…
  </p>
</template>
