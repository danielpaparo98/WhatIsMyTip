<template>
  <!-- PERF-VIEW-UNIFY (2026-10-08): the AFL performance page is now a
       thin wrapper — ALL presentation and data live in the shared
       PerformanceView component, the exact same one every league's
       /{league}/performance page renders. This file owns only the
       route-level concerns that genuinely differ: the AFL SEO/canonical
       (AFL's canonical URL is /performance — there is deliberately no
       /afl/performance route; /afl/performance redirects here via the
       league page's middleware). -->
  <PerformanceView league="afl" />
</template>

<script setup lang="ts">
// FX-05 / FX-20 / H-2: canonical derived from the siteUrl runtime
// config (was hardcoded to the production domain).
const siteUrl = useRuntimeConfig().public.siteUrl as string

// PERF-RENAME (2026-10-07): every SEO/canonical/JSON-LD URL points at
// ${siteUrl}/performance; the /backtest route is a permanent redirect
// (see nuxt.config.ts routeRules).
useSeoMeta({
  title: 'AFL Performance',
  description: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit, accuracy rates, and betting performance across multiple seasons.',
  keywords: 'AFL performance, AFL prediction accuracy, AFL betting performance, AFL tipping results, AFL profit analysis, AFL historical performance',
  ogTitle: 'AFL Performance | AFL Prediction History & Accuracy',
  ogDescription: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit and accuracy rates.',
  ogType: 'website',
  ogUrl: `${siteUrl}/performance`,
  twitterTitle: 'AFL Performance | AFL Prediction History & Accuracy',
  twitterDescription: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit and accuracy rates.',
  twitterCard: 'summary_large_image',
})

useHead({
  link: [
    { rel: 'canonical', href: `${siteUrl}/performance` }
  ],
  script: [
    {
      type: 'application/ld+json',
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'WebPage',
        name: 'AFL Prediction Performance',
        description: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit, accuracy rates, and betting performance.',
        url: `${siteUrl}/performance`,
        mainEntity: {
          '@type': 'Dataset',
          name: 'AFL Prediction Performance Data',
          description: 'Historical performance data for AFL prediction heuristics including accuracy, profit, and betting results'
        }
      })
    }
  ]
})
</script>
