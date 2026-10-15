<template>
  <div class="match-detail-page">
    <!-- LEAGUE-ROUTES (2026-09-30, user request): the /{league}/match/{slug}
         event detail page.  PARITY (2026-10-14): presentation is now
         FIELD-IDENTICAL to /game/[slug].vue — back link, header, WeatherCard,
         Heuristic Tips (AFL tip-card shape with persisted AI explanations),
         Model Predictions (numeric: confidence + margin) and the
         MatchAnalysisCard talking points.  One fetch
         (GET /api/events/{slug}/detail) serves everything, exactly like the
         AFL game page's single detail fetch.  Winner emphasis follows the
         league home's result rows (bold-winner, monochrome) instead of the
         AFL page's accent-free equality between sides. -->
    <!-- Loading State -->
    <div v-if="loading" class="loading" role="status" aria-live="polite">
      <div class="spinner"></div>
      <p>Loading match details...</p>
    </div>

    <!-- Error State -->
    <div v-else-if="error" class="error" role="status" aria-live="polite">
      <h2>Error</h2>
      <p>{{ error }}</p>
      <button @click="retry" class="btn">Retry</button>
      <NuxtLink :to="`/${league}`" class="back-link">← Back to {{ leagueConfig.displayName }}</NuxtLink>
    </div>

    <!-- Match Detail Content -->
    <div v-else-if="event" class="content">
      <NuxtLink :to="`/${league}`" class="back-link">← Back to {{ leagueConfig.displayName }}</NuxtLink>

      <!-- Header Section -->
      <section class="match-header-section">
        <div class="match-info">
          <div class="round-season">
            <span class="round">Round {{ event.round_id ?? '—' }}</span>
            <span class="season">{{ event.season }}</span>
            <span class="status" :class="{ completed: event.completed }">
              {{ statusLabel }}
            </span>
          </div>

          <div class="teams">
            <div class="team home">
              <img :src="teamLogo(homeName)" :alt="`${homeName} badge`" class="team-logo" loading="lazy" decoding="async" width="80" height="80" />
              <span class="team-name" :class="{ winner: homeIsWinner }">{{ getTeamDisplayName(homeName) }}</span>
              <!-- Scores only render once played — an upcoming fixture
                   has no meaningful numbers (same rule as the game page). -->
              <span v-if="event.completed && homeScore !== null" class="score">{{ homeScore }}</span>
            </div>

            <div class="vs">VS</div>

            <div class="team away">
              <img :src="teamLogo(awayName)" :alt="`${awayName} badge`" class="team-logo" loading="lazy" decoding="async" width="80" height="80" />
              <span class="team-name" :class="{ winner: awayIsWinner }">{{ getTeamDisplayName(awayName) }}</span>
              <span v-if="event.completed && awayScore !== null" class="score">{{ awayScore }}</span>
            </div>
          </div>

          <div class="match-meta">
            <div class="meta-item">
              <span class="label">Venue:</span>
              <span class="value">{{ event.venue ?? 'TBD' }}</span>
            </div>
            <div class="meta-item">
              <span class="label">Date:</span>
              <span class="value">{{ event.starts_at ? formatDate(event.starts_at) : 'TBD' }}</span>
            </div>
            <div class="meta-item">
              <span class="label">Time:</span>
              <span class="value">{{ event.starts_at ? formatTime(event.starts_at) : 'TBD' }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- PARITY: Weather Conditions — identical slot to /game/[slug].vue
           (hidden until a forecast exists; the supplementary-sync weather
           pass populates league_event_weather). -->
      <section v-if="detail?.weather" class="match-weather-section">
        <WeatherCard :weather="detail.weather" />
      </section>

      <!-- PARITY: the AFL game page's tipping sections, mirrored —
           Heuristic Tips (the derived heuristics in the AFL tip-card shape
           with persisted AI explanations) then Model Predictions (numeric:
           confidence + margin, exactly like the AFL page's). -->
      <section v-if="derivedTips && derivedTips.length > 0" class="match-tips-section">
        <h2 class="tips-heading">Heuristic Tips</h2>
        <div class="tips-grid">
          <div
            v-for="tip in derivedTips"
            :key="tip.tipKey"
            class="tip-card"
            :class="{ 'tip-selected': tip.isSelection }"
          >
            <div class="tip-card-header">
              <span class="tip-card-heuristic">{{ tip.label }}</span>
              <span v-if="tip.confidence !== null" class="tip-card-confidence">
                {{ Math.round(tip.confidence * 100) }}%
              </span>
            </div>
            <div class="tip-card-body">
              <h3>{{ tip.tipText }}</h3>
              <p v-if="tip.margin !== null" class="tip-card-margin">
                Margin: {{ tip.margin }} pts
              </p>
            </div>
            <p v-if="tip.explanation" class="tip-card-explanation">{{ tip.explanation }}</p>
          </div>
        </div>
      </section>

      <section v-if="modelPredictions && modelPredictions.length > 0" class="match-models-section">
        <h2 class="tips-heading">Model Predictions</h2>
        <div class="models-grid">
          <div v-for="prediction in modelPredictions" :key="prediction.model_name" class="model-card">
            <span class="model-name">{{ modelDisplayName(prediction.model_name) }}</span>
            <h3 class="model-pick">{{ prediction.winner ? getTeamDisplayName(prediction.winner) : '—' }}</h3>
            <p v-if="prediction.confidence !== null" class="model-confidence">
              {{ Math.round(prediction.confidence * 100) }}%
            </p>
            <p v-if="prediction.margin !== null" class="model-margin">
              Margin: {{ prediction.margin }} pts
            </p>
          </div>
        </div>
      </section>

      <!-- PARITY: Talking Points — the same MatchAnalysisCard the AFL game
           page renders (hidden until the nightly sweep has written one). -->
      <section v-if="matchAnalysis" class="match-analysis-section">
        <MatchAnalysisCard :analysis="matchAnalysis" />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
// LEAGUE-ROUTES (2026-09-30, user request): league event detail page.
// Data comes from the ADR 0001 read API (getEvent, useApi.ts); the pure
// route rules (slug shape, league-key registry, cross-league ownership)
// live in composables/useLeagueMatchRoute.ts so vitest can exercise them
// without mounting this SFC.
import type { LeagueEventDetailResponse, SportEvent } from '~/composables/useApi'
import {
  validateLeagueMatchRoute,
  eventMatchesLeague,
  isValidEventSlug,
} from '~/composables/useLeagueMatchRoute'
import { getLeagueConfig, getLeagueHeuristicOptions } from '~/composables/useSportConfig'

definePageMeta({
  // Nuxt's validate() can only accept or 404 a route (no redirects), so
  // the verdicts split across two hooks: validate 404s anything that can
  // never render here (unknown league key, slug violating the backend's
  // 16-char Path constraint); the inline middleware owns the one legal
  // redirect — /afl/match/{slug} back to '/', because AFL matches live
  // on the legacy /game/{slug} surface.
  validate: (route) => {
    return validateLeagueMatchRoute(route.params.league, route.params.slug) !== 'not-found'
  },
  middleware: [
    (to) => {
      const verdict = validateLeagueMatchRoute(to.params.league, to.params.slug)
      if (verdict === 'redirect-home') return navigateTo('/')
    },
  ],
})

const route = useRoute()
const { getEventDetail } = useApi()
const { logoFor } = useTeamIdentity()
const { getTeamDisplayName } = useTeamLogos()
const { formatDate, formatTime } = useFormatters()

const league = computed(() => route.params.league as string)
const slug = computed(() => route.params.slug as string)
const leagueConfig = computed(() => getLeagueConfig(league.value))

// LEAGUE-ROUTES: keep the header league selector in sync with the league
// actually being browsed (the league home page does the same).  Client-only
// so prerendering a match page can never mutate the shared server-side
// league ref and leak into another route's static HTML.
const { setActiveLeague } = useActiveLeague()
if (import.meta.client) {
  watch(league, (key) => setActiveLeague(key), { immediate: true })
}

// PARITY (2026-10-14): ONE fetch serves everything — the
// `/api/events/{slug}/detail` payload is field-identical to the AFL game
// page's `/api/games/{slug}/detail`: event, tips (with persisted AI
// explanations), numeric model_predictions, match_analysis talking points
// and weather.  H-5-revised pattern (mirrors /game/[slug].vue): the
// useAsyncData key is REACTIVE and includes league + slug — every match
// gets its own cache slot, and a key change triggers the fetch
// automatically.  dedupe: 'cancel' drops superseded in-flight fetches.
const {
  data: detail,
  pending: loading,
  error: fetchError,
  refresh: refetch,
} = await useAsyncData<LeagueEventDetailResponse | null>(
  computed(() => `league-match-${league.value}-${slug.value}`),
  async () => {
    // Same pre-flight guard as the game page: reject slugs the backend
    // could never return before spending an API round-trip.
    if (!isValidEventSlug(slug.value)) {
      throw new Error('Invalid event slug')
    }
    const detail = await getEventDetail(slug.value)
    // Cross-league slug-probing guard: /api/events/{slug} resolves ANY
    // league's event, so a WAFL URL must not render a SANFL payload.
    // A mismatch is a genuine 404 (wrong URL), not a transient error —
    // showError renders the global 404 page with the proper status.
    if (!eventMatchesLeague(detail.event, league.value)) {
      showError(createError({ statusCode: 404, statusMessage: 'Match not found' }))
      return null
    }
    // TEAM-IDENTITY (2026-09-30, user request): populate the club-crest
    // identity map on direct loads too — no sport filter (the payload is
    // tiny and the one-shot guard makes repeated visits free). Awaiting
    // it inside this handler means match-page badges show real crests in
    // the prerendered HTML (same SEO-C1 rationale as the league home).
    // NON-FATAL: syncTeamIdentity catches everything; the try/catch is
    // belt-and-suspenders so identity failure never breaks the match.
    try {
      await syncTeamIdentity()
    } catch {
      // Non-fatal by contract — badges fall back to initials.
    }
    return detail
  },
  {
    dedupe: 'cancel',
  },
)

const event = computed(() => detail.value?.event ?? null)

const error = computed<string | null>(() => {
  if (!fetchError.value) return null
  return fetchError.value instanceof Error
    ? fetchError.value.message
    : 'Failed to load match details'
})

// Re-expose a retry for the error state template.
const retry = () => refetch()

// ---------------------------------------------------------------------------
// Side helpers — participants are a list of home/away entries (same shape
// the league home's fixture cards consume).
// ---------------------------------------------------------------------------
const sideOf = (ev: SportEvent, side: string) =>
  ev.participants.find((p) => p.side === side)

const homeSide = computed(() => (event.value ? sideOf(event.value, 'home') ?? null : null))
const awaySide = computed(() => (event.value ? sideOf(event.value, 'away') ?? null : null))

const homeName = computed(() => homeSide.value?.participant_name ?? 'TBD')
const awayName = computed(() => awaySide.value?.participant_name ?? 'TBD')
const homeScore = computed(() => homeSide.value?.score ?? null)
const awayScore = computed(() => awaySide.value?.score ?? null)
const homeIsWinner = computed(() => homeSide.value?.is_winner === true)
const awayIsWinner = computed(() => awaySide.value?.is_winner === true)

// Status pill: the events API distinguishes cancelled/void fixtures, which
// the AFL game page never sees.  "Upcoming" for a cancelled match would be
// misleading, so the raw status is surfaced (capitalised) verbatim.
const statusLabel = computed(() => {
  const ev = event.value
  if (!ev) return ''
  if (ev.completed) return 'Completed'
  const raw = ev.status || 'upcoming'
  return raw.charAt(0).toUpperCase() + raw.slice(1)
})

// ---------------------------------------------------------------------------
// PARITY tips + models: the detail payload's tips carry the AFL card shape
// (picked/explanation/confidence/margin) for EVERY stored heuristic; the
// derived four render as Heuristic Tips, and the numeric
// model_predictions rows render as Model Predictions — the exact split
// the AFL game page uses.  The tips render in the league's canonical
// heuristic order with the selected heuristic highlighted as the primary
// row.
// ---------------------------------------------------------------------------
const selectedHeuristic = ref<string>('best_bet')
onMounted(() => {
  const stored = localStorage.getItem('selected-heuristic')
  const legal = getLeagueHeuristicOptions(league.value).some(
    (h) => h.value === stored,
  )
  if (stored && legal && stored !== selectedHeuristic.value) {
    selectedHeuristic.value = stored
  }
})

const DERIVED_SET = new Set(['best_bet', 'weighted_tip', 'boosted_tip', 'yolo'])

const derivedTips = computed(() => {
  const rows = detail.value?.tips
  if (!rows || rows.length === 0) return null
  const options = getLeagueHeuristicOptions(league.value)
  const labelFor = new Map(options.map((h) => [h.value, h.label]))
  const orderIndex = new Map(options.map((h, i) => [h.value, i]))
  return rows
    .filter((row) => DERIVED_SET.has(row.heuristic))
    .map((row) => ({
      tipKey: row.heuristic,
      label: labelFor.get(row.heuristic) ?? row.heuristic.replaceAll('_', ' '),
      tipText:
        row.picked === null ? 'Draw' : getTeamDisplayName(row.picked!),
      isSelection: row.heuristic === selectedHeuristic.value,
      confidence: row.confidence,
      margin: row.margin,
      explanation: row.explanation,
      rank: orderIndex.get(row.heuristic) ?? options.length,
    }))
    .sort((a, b) => a.rank - b.rank)
})

// PARITY: the numeric model breakdown — every stored prediction renders
// with the same confidence % / margin numbers as the AFL page's cards,
// labelled through the league's (six-model) display-name map.
const modelPredictions = computed(() => detail.value?.model_predictions ?? null)

// PARITY: the AFL MatchAnalysisCard consumes the AFL analysis interface
// (keyed `game_id`); the league payload keys `event_id`. The card reads
// `analysis_text` only — map the key so the AFL component renders the
// league talking points unmodified.
const matchAnalysis = computed(() => {
  const analysis = detail.value?.match_analysis
  if (!analysis) return null
  return { ...analysis, game_id: analysis.event_id }
})

const modelDisplayName = (modelName: string): string =>
  leagueConfig.value.modelDisplayNames[modelName] ?? modelName.replaceAll('_', ' ')

// Badges resolve through the shared identity chain (explicit entry → AFL
// map → generated initials badge for state-league clubs, colored via the
// league's club palette); unknown names keep the neutral placeholder.
const teamLogo = (name: string): string => logoFor(name, league.value) ?? ''

// FX-05 / FX-20 pattern (mirrors /game/[slug].vue): per-match SEO +
// canonical URL, set up front so the canonical appears in the SSR head
// regardless of fetch timing.  No "| WhatIsMyTip" suffix in title strings —
// titleTemplate in nuxt.config already appends it.
const canonicalBase = useRuntimeConfig().public.siteUrl
const canonicalUrl = computed(() =>
  `${canonicalBase}/${league.value}/match/${slug.value}`
)

useSeoMeta({
  title: () => event.value
    ? `${homeName.value} vs ${awayName.value} — ${leagueConfig.value.displayName}`
    : 'Match Details',
  description: () => {
    const ev = event.value
    if (!ev) {
      return `${leagueConfig.value.displayName} match details, venue and start time from WhatIsMyTip.`
    }
    const state = ev.completed ? 'final result' : 'fixture'
    return `${homeName.value} vs ${awayName.value} (${state}, Round ${ev.round_id ?? '—'}, ${ev.season}) — ${leagueConfig.value.displayName} venue, date, time and result.`
  },
  ogTitle: () => event.value
    ? `${homeName.value} vs ${awayName.value} — ${leagueConfig.value.displayName}`
    : 'Match Details',
  ogDescription: () => {
    const ev = event.value
    return ev
      ? `${leagueConfig.value.displayName}: ${homeName.value} vs ${awayName.value} at ${ev.venue ?? 'a venue to be confirmed'}.`
      : `${leagueConfig.value.displayName} match details and result.`
  },
  ogType: 'website',
  ogUrl: canonicalUrl,
  twitterTitle: () => event.value
    ? `${homeName.value} vs ${awayName.value} — ${leagueConfig.value.displayName}`
    : 'Match Details',
  twitterDescription: () => {
    const ev = event.value
    return ev
      ? `${leagueConfig.value.displayName}: ${homeName.value} vs ${awayName.value} at ${ev.venue ?? 'a venue to be confirmed'}.`
      : `${leagueConfig.value.displayName} match details and result.`
  },
  twitterCard: 'summary_large_image',
})

useHead({
  link: [
    { rel: 'canonical', href: canonicalUrl }
  ]
})
</script>

<style scoped>
.match-detail-page {
  min-height: 100vh;
  padding: 2rem 1rem;
}

/* Loading State */
.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  gap: 1rem;
}

.spinner {
  width: 48px;
  height: 48px;
  border: 4px solid var(--color-border);
  border-top-color: var(--color-text);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.loading p {
  color: var(--color-muted);
}

/* Error State */
.error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  gap: 1rem;
  text-align: center;
}

.error h2 {
  color: var(--color-error);
  margin: 0;
}

.error p {
  color: var(--color-muted);
  margin: 0;
}

/* Back Link */
.back-link {
  display: inline-flex;
  align-items: center;
  color: var(--color-muted);
  text-decoration: none;
  transition: color 0.2s ease;
  font-weight: 500;
  /* 44px minimum touch target (a11y requirement). */
  min-height: 44px;
  padding: 0.5rem 0.75rem;
  margin-bottom: 1rem;
  border: 1px solid transparent;
  border-radius: 4px;
}

.back-link:hover {
  border-color: var(--color-border);
  color: var(--color-text);
}

/* Focus and touch-target parity for the retry button (the global .btn
   style predates the 44px a11y rule; this page enforces it locally). */
.btn {
  min-height: 44px;
}

.back-link:focus-visible,
.btn:focus-visible {
  outline: 2px solid var(--color-text);
  outline-offset: 2px;
}

/* Content */
.content {
  max-width: 1200px;
  margin: 0 auto;
}

/* Match Header Section */
.match-header-section {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  padding: 2rem;
  margin-bottom: 2rem;
}

.match-info {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.round-season {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  align-items: center;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.round {
  font-weight: 700;
  font-size: 1.125rem;
}

.season {
  color: var(--color-muted);
  font-size: 0.9375rem;
}

.status {
  padding: 0.25rem 0.75rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  background: var(--color-muted);
  color: var(--color-bg);
}

.status.completed {
  /* Monochrome pill — same treatment as the game page. */
  background: var(--color-text);
  color: var(--color-bg);
}

/* FCL-12 forward tips — monochrome, same card language as the header. */
.match-weather-section {
  margin-bottom: 2rem;
}

.match-tips-section {
  margin-top: 1.5rem;
  border: 1px solid var(--color-border);
  padding: 1.5rem;
}

.tips-heading {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
  margin-bottom: 1rem;
}

.tips-list {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

/* FCL-14: the AFL game page's tipping sections — tip cards with the
   AFL card treatment (monochrome spine, badge/confidence/winner/
   margin/explanation) and the model-predictions grid. */
.tips-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1rem;
}

.tip-card {
  border: 1px solid var(--color-border);
  border-left: 4px solid var(--color-text);
  padding: 1.5rem;
}

.tip-card.tip-selected {
  /* The user's selected heuristic is the primary card. */
  background: var(--color-hover);
}

.tip-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.875rem;
  padding-bottom: 0.875rem;
  border-bottom: 1px solid var(--color-border);
}

.tip-card-heuristic {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.tip-card-confidence {
  font-size: 0.8125rem;
  font-weight: 700;
}

.tip-card-body h3 {
  font-size: 1.25rem;
  margin-bottom: 0.5rem;
}

.tip-card-margin {
  font-size: 0.8125rem;
  margin: 0;
}

.tip-card-explanation {
  margin-top: 0.875rem;
  font-size: 0.875rem;
  color: var(--color-muted);
}

.models-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1rem;
}

.model-card {
  border: 1px solid var(--color-border);
  padding: 1.5rem;
  transition: border-color 0.2s ease;
}

.model-card:hover {
  border-color: var(--color-text);
}

.model-name {
  display: block;
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
  margin-bottom: 0.75rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--color-border);
}

.model-pick {
  font-size: 1.25rem;
  margin: 0;
}

/* PARITY: the AFL game page's numeric model cards — confidence % and
   margin ride under the pick, same treatment as the tip cards. */
.model-confidence {
  font-size: 0.8125rem;
  font-weight: 700;
  margin: 0.5rem 0 0;
}

.model-margin {
  font-size: 0.8125rem;
  margin: 0.25rem 0 0;
}

.match-analysis-section {
  margin-top: 1.5rem;
}

/* Teams */
.teams {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2rem;
}

.team {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  flex: 1;
}

.team-logo {
  width: 80px;
  height: 80px;
  object-fit: contain;
}

.team-name {
  font-weight: 700;
  font-size: 1.125rem;
  text-align: center;
}

/* Winner emphasis mirrors the league home's result rows (bold-winner,
   monochrome — no accent colour). */
.team-name.winner {
  font-weight: 800;
}

.score {
  font-size: 2rem;
  font-weight: 800;
  margin-top: 0.25rem;
}

.vs {
  font-size: 1.125rem;
  font-weight: 700;
  color: var(--color-muted);
}

/* Match Meta */
.match-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--color-border);
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.meta-item .label {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-muted);
}

.meta-item .value {
  font-weight: 600;
}

/* Mobile Responsive */
@media (max-width: 640px) {
  .match-detail-page {
    padding: 1rem 0.75rem;
  }

  .match-header-section {
    padding: 1.5rem 1rem;
  }

  .teams {
    gap: 1rem;
  }

  .team-logo {
    width: 64px;
    height: 64px;
  }

  .team-name {
    font-size: 1rem;
  }

  .score {
    font-size: 1.5rem;
  }

  .vs {
    font-size: 1rem;
  }
}

/* Tablet Responsive */
@media (min-width: 641px) and (max-width: 1024px) {
  .team-logo {
    width: 72px;
    height: 72px;
  }

  .team-name {
    font-size: 1.0625rem;
  }

  .score {
    font-size: 1.75rem;
  }
}

/* Desktop Responsive */
@media (min-width: 1025px) {
  .match-header-section {
    padding: 2.5rem;
  }

  .team-logo {
    width: 96px;
    height: 96px;
  }

  .team-name {
    font-size: 1.25rem;
  }

  .score {
    font-size: 2.25rem;
  }
}
</style>
