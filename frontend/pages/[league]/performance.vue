<template>
  <!-- =====================================================================
       PERF-PER-LEAGUE (2026-10): first-class /{league}/performance page.
       The route contract is copied from pages/[league]/index.vue (the
       league route family's single reference): validate() against the
       LEAGUES registry, an inline middleware that hands 'afl' back to
       the root-level /performance page, client-only active-league sync,
       and a leagueConfig resolved from the ROUTE param — never the
       global active-league store, because the prerender bakes every
       league page in one process.

       Presentation: the AFL performance page's two-season summary
       (current + most recent past season, per-heuristic accuracy /
       profit / rounds) minus the AFL-only sections. Leagues without
       tips yet — unsynced ones included — render the approved graceful
       empty state.
       ===================================================================== -->
  <section class="hero">
    <h1>{{ leagueConfig.displayName }}<br>Performance</h1>
    <p>How our tipping heuristics are performing.</p>
  </section>

  <div v-if="pending" class="loading" role="status" aria-live="polite">
    <div class="spinner"></div>
  </div>

  <!-- No tips yet / league not synced / fetch failed (all degrade here):
       the approved empty state. A registered-but-unsynced league gets a
       zero payload (only unknown keys 404, and validate() blocks those),
       so "isn't available yet" is truthful when NO payload arrived —
       while a synced league whose season hasn't started gets its own
       copy below. The page never breaks. -->
  <div v-else-if="!hasPerformanceData" class="empty" role="status" aria-live="polite">
    <template v-if="currentSeason">
      <p>The {{ currentSeason.season }} season hasn't started yet.</p>
      <p class="empty-hint">Performance tracking begins once Round 1 tips are graded.</p>
    </template>
    <template v-else>
      <p>Performance tracking isn't available for {{ leagueConfig.displayName }} yet.</p>
      <p class="empty-hint">Performance tracking begins once tipping models are live for this league.</p>
    </template>
  </div>

  <template v-else>
    <!-- Current season: per-heuristic accuracy / profit / rounds, the
         same card language as the AFL page (monochrome league styling). -->
    <section class="section">
      <div class="season-header">
        <h2><span class="badge">Current Season {{ currentSeason?.season }}</span></h2>
        <p class="season-progress">
          {{ currentSeason?.rounds_completed }} / {{ currentSeason?.total_rounds }} rounds completed
        </p>
      </div>

      <div class="profit-basis">
        <p>
          <strong>How profit is calculated:</strong> each tip carries a simulated
          $10 stake — settled at real odds where available and a representative
          $1.90 otherwise; drawn games refund the stake. <em>Odds coverage</em>
          is the share of tips settled at real prices.
        </p>
      </div>

      <div class="season-cards">
        <article
          v-for="h in currentHeuristics"
          :key="h.heuristic"
          class="season-card"
        >
          <div class="card-header">
            <h3>{{ heuristicLabel(h.heuristic) }}</h3>
            <span class="heuristic-badge">Current Season</span>
          </div>
          <div class="card-stats">
            <div class="stat-row">
              <span class="stat-label">Season Profit</span>
              <span class="stat-value">{{ formatProfit(h.total_profit) }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Accuracy</span>
              <span class="stat-value">
                {{ formatPct(h.rounds_played > 0 ? h.total_accuracy : null) }}
              </span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Rounds Played</span>
              <span class="stat-value">{{ h.rounds_played }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Avg Profit/Round</span>
              <span class="stat-value">{{ formatProfit(h.avg_profit_per_round) }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Odds Coverage</span>
              <span class="stat-value">{{ formatCoverage(h.odds_coverage) }}</span>
            </div>
          </div>
        </article>
      </div>
    </section>

    <!-- Most recent past season: hidden entirely when the league has no
         prior season (or the comparison fetch failed) — additive only. -->
    <section v-if="pastSeasonEntries.length > 0" class="section">
      <div class="season-header">
        <h2>
          <span class="badge">Most Recent Past Season {{ pastSeasonLabel }}</span>
        </h2>
      </div>

      <div class="season-cards">
        <article
          v-for="entry in pastSeasonEntries"
          :key="entry.heuristic"
          class="season-card"
        >
          <div class="card-header">
            <h3>{{ heuristicLabel(entry.heuristic) }}</h3>
            <span class="heuristic-badge">{{ pastSeasonLabel }}</span>
          </div>
          <div class="card-stats">
            <div class="stat-row">
              <span class="stat-label">Accuracy</span>
              <span class="stat-value">{{ formatPct(entry.overall_accuracy) }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Season Profit</span>
              <span class="stat-value">{{ formatProfit(entry.total_profit) }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Tips Correct</span>
              <span class="stat-value">{{ entry.total_correct }} / {{ entry.total_tips }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Rounds</span>
              <span class="stat-value">{{ entry.total_rounds }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Avg Profit/Round</span>
              <span class="stat-value">{{ formatProfit(entry.avg_profit_per_round) }}</span>
            </div>
          </div>
        </article>
      </div>
    </section>
  </template>
</template>

<script setup lang="ts">
// PERF-PER-LEAGUE (2026-10): league performance view. Data comes from
// the league-aware backtest API (D4) through the subtask-09 useApi
// methods; the past-season selection rule (max year < current) lives
// in the dependency-free lib/performanceSeasons.ts so tests and future
// callers share one implementation.
import { LEAGUES, getLeagueConfig } from '~/composables/useSportConfig'
import { sortByHeuristicOrder } from '~/composables/useFormatters'
import { mostRecentPastSeasonYear } from '~/lib/performanceSeasons'
import type {
  LeagueCurrentSeasonResponse,
  LeagueCurrentSeasonHeuristicPerformance,
  LeagueComparisonResponse,
  LeagueHeuristicSeasonStats,
} from '~/composables/useApi'

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

// Per-league presentation config resolved from the ROUTE (not the
// global active-league store): the prerender bakes every league page
// in one process, so a globally-scoped selection would leak another
// league's labels into the static HTML.
const leagueConfig = computed(() => getLeagueConfig(leagueKey.value ?? 'afl'))

// ---------------------------------------------------------------------------
// Data — league backtest payloads through an awaited useAsyncData so
// the fetch runs AT GENERATE TIME and inlines the payload into the
// prerendered HTML (the SEO-C1 contract documented on the league
// home). Reactive per-league cache key + dedupe: 'cancel' — a route
// param change refetches and a superseded in-flight fetch is dropped
// (the game/match pages' H-5 contract, same as useLeagueEvents).
//
// Degradation: an unsynced league's zero payload (or an unexpected 404
// / failure) degrades to null so the page renders its empty state
// instead of failing; the seasons→compare chain for the past season is
// additive and its own try/catch hides only that section on failure.
// ---------------------------------------------------------------------------
const api = useApi()

/** What the useAsyncData handler resolves to. */
interface LeaguePerformancePayload {
  current: LeagueCurrentSeasonResponse | null
  past: LeagueComparisonResponse | null
}

const fetchLeaguePerformance = async (): Promise<LeaguePerformancePayload> => {
  const league = leagueKey.value
  // 'afl' never reaches this page (middleware redirect); guarded for
  // the same totality as the league home's fetch.
  if (!league || league === 'afl') {
    return { current: null, past: null }
  }
  // 404 (league not synced yet) and any other failure degrade to the
  // approved empty state — a missing league must never break the page.
  const current = await api
    .getLeagueCurrentSeasonPerformance(league)
    .catch(() => null)
  if (!current || current.heuristics.length === 0) {
    return { current: null, past: null }
  }
  let past: LeagueComparisonResponse | null = null
  try {
    const seasons = await api.getLeagueSeasons(league)
    const prevSeason = mostRecentPastSeasonYear(
      seasons.available_years,
      seasons.current_year,
    )
    if (prevSeason !== null) {
      past = await api.getLeagueComparison(league, prevSeason)
    }
  } catch {
    past = null
  }
  return { current, past }
}

const { data, pending, refresh } = await useAsyncData<LeaguePerformancePayload>(
  computed(() => `league-performance-${leagueKey.value ?? 'none'}`),
  fetchLeaguePerformance,
  { dedupe: 'cancel' },
)

// Client-side freshness on hydrated visits: the inlined payload renders
// the first paint, then ONE background refresh updates the numbers —
// same contract as useLeagueEvents (no refetch during prerender/SSR).
if (import.meta.client) {
  onNuxtReady(() => {
    void refresh()
  })
}

// View state derived from the payload. The data gate mirrors the AFL
// page's hasSeasonResults (review #3): the league zero payload always
// CONTAINS three zeroed heuristics, so heuristics.length alone would
// render "$0.00 / 0%" cards for a synced-but-unplayed league. Data is
// present only once a round has actually been graded.
const currentSeason = computed(() => data.value?.current ?? null)
const pastSeason = computed(() => data.value?.past ?? null)
const hasPerformanceData = computed(
  () =>
    (currentSeason.value?.rounds_completed ?? 0) > 0 ||
    (currentSeason.value?.heuristics.some((h) => h.rounds_played > 0) ?? false),
)

const currentHeuristics = computed<LeagueCurrentSeasonHeuristicPerformance[]>(() =>
  sortByHeuristicOrder(currentSeason.value?.heuristics ?? []),
)

/** One per-heuristic entry of the past-season comparison dict. */
interface PastSeasonEntry extends LeagueHeuristicSeasonStats {
  heuristic: string
}

const pastSeasonEntries = computed<PastSeasonEntry[]>(() => {
  const comparison = pastSeason.value?.comparison
  if (!comparison) return []
  return sortByHeuristicOrder(
    Object.entries(comparison).map(([heuristic, stats]) => ({
      heuristic,
      ...stats,
    })),
  )
})

const pastSeasonLabel = computed(() => String(pastSeason.value?.season ?? ''))

// ---------------------------------------------------------------------------
// Presentation helpers (pure).
//
// Labels: the league heuristics (home_advantage / form / ladder) have
// no entry in the AFL-shaped HEURISTIC_LABELS, so they are mapped here
// and fall back to the shared formatter for anything else.
// Profit: monochrome league styling — explicit +/− prefixes instead of
// the AFL page's red/green accents.
// ---------------------------------------------------------------------------
const LEAGUE_HEURISTIC_LABELS: Record<string, string> = {
  home_advantage: 'Home Advantage',
  form: 'Form',
  ladder: 'Ladder',
}

const { formatHeuristic } = useFormatters()

const heuristicLabel = (heuristic: string): string =>
  LEAGUE_HEURISTIC_LABELS[heuristic] ?? formatHeuristic(heuristic)

const formatPct = (fraction: number | null | undefined): string =>
  typeof fraction === 'number' && Number.isFinite(fraction)
    ? `${(fraction * 100).toFixed(1)}%`
    : '—'

const formatCoverage = (fraction: number | null | undefined): string =>
  typeof fraction === 'number' && Number.isFinite(fraction)
    ? `${Math.round(fraction * 100)}%`
    : '—'

const formatProfit = (value: number): string =>
  `${value < 0 ? '-' : '+'}$${Math.abs(value).toFixed(2)}`

// ---------------------------------------------------------------------------
// SEO — same pattern as the league home: state-aware getters keyed to
// the league displayName, a canonical <link> through useHead
// (useSeoMeta alone cannot set canonicals), and a JSON-LD WebPage for
// the per-league canonical URL. titleTemplate (nuxt.config) appends
// the site name.
// ---------------------------------------------------------------------------
const siteUrl = useRuntimeConfig().public.siteUrl as string
const leagueName = computed(() => leagueConfig.value.displayName)
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

<style scoped>
/* Hero — the league home's spacing rhythm, league-scoped copy. */
.hero {
  padding: 3.5rem 1.5rem 2.25rem;
  text-align: center;
}

.hero h1 {
  font-size: clamp(2rem, 8vw, 6rem);
  line-height: 1.05;
  margin-bottom: 1rem;
}

.hero p {
  font-size: 1.125rem;
  max-width: 600px;
  margin: 0 auto;
}

.section {
  padding: 2.25rem 1.5rem 3rem;
}

/* Season header — inverted badge, the AFL page's season marker in the
   league pages' monochrome language. */
.season-header {
  text-align: center;
  margin-bottom: 1.5rem;
}

.badge {
  display: inline-block;
  padding: 0.5rem 1.25rem;
  background: var(--color-text);
  color: var(--color-bg);
  font-weight: 800;
  font-size: 1rem;
  letter-spacing: 0.02em;
}

.season-progress {
  margin-top: 0.75rem;
  font-size: 0.875rem;
  color: var(--color-muted);
}

/* Profit basis explainer — parity with the AFL page's settlement note. */
.profit-basis {
  border: 1px solid var(--color-border);
  padding: 1rem 1.25rem;
  margin-bottom: 1.5rem;
  font-size: 0.875rem;
  color: var(--color-muted);
  max-width: 900px;
  margin-left: auto;
  margin-right: auto;
}

.profit-basis p {
  margin: 0;
}

.profit-basis strong {
  color: var(--color-text);
}

/* Season cards */
.season-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 1.5rem;
}

.season-card {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  padding: 1.5rem;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

.dark .season-card {
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.4);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 0.75rem;
  padding-bottom: 0.875rem;
  margin-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
  flex-wrap: wrap;
}

.card-header h3 {
  font-size: 1.125rem;
  font-weight: 800;
  margin: 0;
}

.heuristic-badge {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.card-stats {
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.stat-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 1rem;
}

.stat-label {
  font-size: 0.8125rem;
  color: var(--color-muted);
}

.stat-value {
  font-size: 1rem;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}

/* States — identical contract to the league home. */
.loading, .error, .empty {
  text-align: center;
  padding: 3rem 1.5rem;
}

.empty-hint {
  margin-top: 0.5rem;
  font-size: 0.875rem;
  color: var(--color-muted);
}

/* Mobile styles */
@media (max-width: 640px) {
  .hero {
    padding: 2.5rem 1rem 1.75rem;
  }

  .hero p {
    font-size: 1rem;
  }

  .section {
    padding: 2rem 1rem;
  }

  .season-cards {
    grid-template-columns: 1fr;
    gap: 1rem;
  }

  .season-card {
    padding: 1.25rem;
  }

  .badge {
    font-size: 0.875rem;
    padding: 0.375rem 1rem;
  }

  .loading, .error, .empty {
    padding: 2rem 1rem;
  }

  .profit-basis {
    padding: 0.875rem 1rem;
  }
}

/* Tablet styles */
@media (min-width: 641px) and (max-width: 1024px) {
  .hero {
    padding: 5rem 1.5rem;
  }

  .season-cards {
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  }
}

/* Desktop styles */
@media (min-width: 1025px) {
  .hero {
    padding: 5rem 2rem 3rem;
  }

  .hero h1 {
    font-size: clamp(3rem, 10vw, 6rem);
    line-height: 0.95;
  }

  .hero p {
    font-size: 1.25rem;
  }

  .section {
    padding: 4rem 2rem;
    max-width: 1200px;
    margin: 0 auto;
  }

  .season-cards {
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 2rem;
  }

  .season-card {
    padding: 2rem;
  }

  .badge {
    font-size: 1.125rem;
  }
}
</style>
