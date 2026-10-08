<template>
  <!-- PERF-VIEW-UNIFY (2026-10-08): the league performance page is now a
       thin wrapper — ALL presentation and data live in the shared
       PerformanceView component, the exact same one the AFL page at
       /performance renders. This file owns only the route-level
       concerns that genuinely differ: the route contract, the
       active-league sync, and the per-league SEO/canonical. -->
  <PerformanceView v-if="leagueKey" :league="leagueKey" />
</template>

<script setup lang="ts">
import { LEAGUES } from '~/composables/useSportConfig'

// ---------------------------------------------------------------------------
// Route contract (copied from pages/[league]/index.vue)
//
// validate(): unknown league keys are not league pages at all — they
// must 404 rather than render a generic shell. Inline middleware:
// '/afl/performance' is not a route — the AFL keeps its performance
// page at the root URL, so the 'afl' key canonicalises to
// /performance (the /-home analogue of the league home's '/' rule).
// ---------------------------------------------------------------------------

definePageMeta({
  validate: (route) => {
    const raw = route.params.league
    const key = Array.isArray(raw) ? raw[0] : raw
    return typeof key === 'string' && LEAGUES.some((l) => l.key === key)
  },
  middleware: [
    function aflOwnsRootPerformance(to) {
      const raw = to.params.league
      const key = Array.isArray(raw) ? raw[0] : raw
      if (key === 'afl') {
        return navigateTo('/performance', { replace: true })
      }
    },
  ],
})

const route = useRoute()

/** The league key from the URL, or null before a param exists. */
const leagueKey = computed<string | null>(() => {
  const raw = route.params.league
  const key = Array.isArray(raw) ? raw[0] : raw
  return typeof key === 'string' && key.length > 0 ? key : null
})

// ---------------------------------------------------------------------------
// Active-league sync (same contract as the league home): visiting
// /{league}/performance must move the header dropdown with the route.
// Client-only — mutating the module-scoped league store during
// prerender would leak one league's selection into every static page.
// ---------------------------------------------------------------------------
const { setActiveLeague } = useActiveLeague()
if (import.meta.client && leagueKey.value) {
  setActiveLeague(leagueKey.value)
}
watch(leagueKey, (key) => {
  if (import.meta.client && key) setActiveLeague(key)
})

// ---------------------------------------------------------------------------
// SEO — same pattern as the league home: state-aware getters keyed to
// the league displayName, a canonical <link> through useHead
// (useSeoMeta alone cannot set canonicals), and a JSON-LD WebPage for
// the per-league canonical URL. titleTemplate (nuxt.config) appends
// the site name. The page deliberately contains NO data fetching —
// PerformanceView owns it (per-league cache keys inside the component).
// ---------------------------------------------------------------------------
const siteUrl = useRuntimeConfig().public.siteUrl as string
const leagueName = computed(() => getLeagueConfig(leagueKey.value ?? 'afl').displayName)
const canonicalHref = computed(
  () => `${siteUrl}/${leagueKey.value ?? ''}/performance`,
)

useSeoMeta({
  title: () => `${leagueName.value} Performance`,
  description: () =>
    `Historical accuracy and profit of the ${leagueName.value} tipping heuristics — tracked season by season.`,
  ogTitle: () => `${leagueName.value} Performance`,
  ogDescription: () =>
    `Historical accuracy and profit of the ${leagueName.value} tipping heuristics.`,
  ogType: 'website',
  ogUrl: () => canonicalHref.value,
  twitterTitle: () => `${leagueName.value} Performance`,
  twitterDescription: () =>
    `Historical accuracy and profit of the ${leagueName.value} tipping heuristics.`,
})

useHead({
  link: [
    { rel: 'canonical', href: () => canonicalHref.value },
  ],
  script: [
    {
      type: 'application/ld+json',
      innerHTML: () =>
        JSON.stringify({
          '@context': 'https://schema.org',
          '@type': 'WebPage',
          name: `${leagueName.value} Performance`,
          description: `Historical accuracy and profit of the ${leagueName.value} tipping heuristics.`,
          url: canonicalHref.value,
        }),
    },
  ],
})
</script>
