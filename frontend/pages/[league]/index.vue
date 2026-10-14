<template>
  <!-- =====================================================================
       LEAGUE-ROUTES (2026-09-30, user request): first-class /{league}
       home page. Every non-AFL league gets its own URL with the same
       presentation quality as the AFL home — identical hero, club
       badges on every card, clickable cards, and the end-of-season
       (grand final / season complete) treatment instead of a bare
       "last round results" list.
       ===================================================================== -->
  <section class="hero">
    <h1>AI-Powered<br>Footy Tipping</h1>
    <p>Smart heuristics. Clear explanations. Better tips.</p>
  </section>

  <div v-if="pending" class="loading" role="status" aria-live="polite">
    <div class="spinner"></div>
  </div>

  <div v-else-if="error" class="error" role="status" aria-live="polite">
    <p>{{ error }}</p>
    <button @click="refresh" class="btn">Retry</button>
  </div>

  <!-- League not synced yet (no competition/season in /api/sports) -->
  <div v-else-if="unavailable" class="empty" role="status" aria-live="polite">
    <p>No {{ leagueConfig.displayName }} fixtures have been synced yet.</p>
    <p class="empty-hint">Fixtures land automatically once the league's data source is live.</p>
  </div>

  <template v-else>
    <!-- End of season: the premier celebration carries the same
         ALWAYS-HERO convention as the AFL post-season state (the hero
         above never unmounts); the final round's results stay below so
         the page keeps its substance. -->
    <template v-if="isSeasonComplete">
      <OffSeasonCelebration
        :premier="premier"
        :season="seasonNumber"
        :league="leagueKey"
      />
    </template>

    <section class="section">
      <!-- Round strip: "GF • {season}" for the season's final round
           (upcoming or played), "R{n} • {season}" for regular rounds. -->
      <div v-if="roundId !== null" class="round-display">
        <span class="round-label">{{ leagueConfig.displayName }}</span>
        <span class="round-value">{{ roundStrip }}</span>
        <span class="game-count">{{ roundEvents.length }} {{ leagueConfig.contestNoun }}s</span>
      </div>

      <div v-if="roundEvents.length === 0" class="empty" role="status" aria-live="polite">
        <p>No fixtures found for this round yet.</p>
      </div>
      <template v-else>
        <!-- FCL-13: the AFL home's user-selectable heuristic, mirrored —
             the selected tab drives which stored pick each upcoming
             card shows (no refetch; the payload carries every pick). -->
        <div class="heuristic-selector" role="tablist" aria-label="Heuristic filter">
          <button
            v-for="h in heuristicOptions"
            :key="h.value"
            role="tab"
            :aria-selected="selectedHeuristic === h.value"
            @click="selectedHeuristic = h.value"
            :class="['heuristic-btn', { active: selectedHeuristic === h.value }]"
          >
            {{ h.label }}
          </button>
        </div>

        <Transition name="fade" mode="out-in">
          <div :key="selectedHeuristic" class="games-grid">
            <NuxtLink
              v-for="ev in roundEvents"
              :key="ev.slug"
              :to="`/${leagueKey}/match/${ev.slug}`"
              class="game-card-link"
            >
              <article class="game-card">
                <div class="match-info">
                  <div class="teams">
                    <div class="team home">
                      <img :src="sideLogo(ev, 'home')" :alt="`${sideName(ev, 'home')} logo`" class="team-logo" loading="lazy" decoding="async" width="40" height="40" />
                      <span class="team-name">{{ displayName(sideName(ev, 'home')) }}</span>
                    </div>
                    <span class="vs">VS</span>
                    <div class="team away">
                      <img :src="sideLogo(ev, 'away')" :alt="`${sideName(ev, 'away')} logo`" class="team-logo" loading="lazy" decoding="async" width="40" height="40" />
                      <span class="team-name">{{ displayName(sideName(ev, 'away')) }}</span>
                    </div>
                  </div>
                  <div class="match-details">
                    <span class="venue">{{ ev.venue ?? 'TBD' }}</span>
                    <span class="date">{{ ev.starts_at ? formatDate(ev.starts_at) : 'TBD' }}</span>
                  </div>
                </div>

                <!-- Results when played; the SELECTED heuristic's tip in
                     the AFL card shape (FCL-14: badge, confidence,
                     winner, margin); "No tip available" when the
                     heuristic has no stored tip — byte-identical to
                     the AFL home's card states. -->
                <div v-if="ev.completed" class="tip-info league-result">
                  <div class="result-row">
                    <span class="result-team" :class="{ winner: sideIsWinner(ev, 'home') }">
                      {{ sideName(ev, 'home') }}
                    </span>
                    <span class="result-score">{{ sideScore(ev, 'home') ?? '—' }}</span>
                  </div>
                  <div class="result-row">
                    <span class="result-team" :class="{ winner: sideIsWinner(ev, 'away') }">
                      {{ sideName(ev, 'away') }}
                    </span>
                    <span class="result-score">{{ sideScore(ev, 'away') ?? '—' }}</span>
                  </div>
                </div>
                <div v-else-if="pickFor(ev)" class="tip-info">
                  <div class="tip-header">
                    <span class="heuristic-badge">{{ heuristicLabel }}</span>
                    <span
                      v-if="pickFor(ev)!.confidence !== null"
                      class="confidence"
                    >{{ Math.round(pickFor(ev)!.confidence! * 100) }}%</span>
                  </div>
                  <div class="tip-body">
                    <h3>{{ displayName(pickFor(ev)!.name) }}</h3>
                    <p v-if="pickFor(ev)!.margin !== null" class="margin">
                      Margin: {{ pickFor(ev)!.margin }} pts
                    </p>
                  </div>
                </div>
                <div v-else class="no-tip">
                  <p>No tip available</p>
                </div>
              </article>
            </NuxtLink>
          </div>
        </Transition>
      </template>
    </section>
  </template>

  <!-- GF-DESIGN parity: once the season is complete, confetti fires in
       BOTH finalists' club colours (curated league palettes). The
       `active` prop opts the league page out of ConfettiEffect's AFL
       grand-final gate — the AFL home keeps driving it from
       latestRound, untouched. -->
  <ConfettiEffect
    v-if="isSeasonComplete"
    :colors="confettiColors ?? undefined"
    active
  />
</template>

<script setup lang="ts">
// LEAGUE-ROUTES (2026-09-30, user request): this page replaces the
// league branch of the home view with a first-class route. The AFL
// keeps '/' (its home states are untouched); 'afl' visited here is
// canonicalised back to '/' so the AFL home stays single-sourced.
import { LEAGUES, getLeagueConfig, getLeagueHeuristicOptions } from '~/composables/useSportConfig'
import { useLeagueEvents } from '~/composables/useLeagueEvents'
import { useLeagueRoundTips } from '~/composables/useLeagueRoundTips'
import type { SportEvent } from '~/composables/useApi'
import { derivePremier, deriveSeasonState } from '~/composables/useSeasonState'
import { leagueColorFor } from '~/composables/useLeagueColors'

// ---------------------------------------------------------------------------
// Route contract
//
// validate(): unknown league keys are not league pages at all — they
// must 404 (e.g. /favicon.ico-style requests that reach the dynamic
// segment) rather than render a generic shell. Inline middleware:
// '/afl' redirects to '/' (AFL stays at the root URL, user-approved).
// ---------------------------------------------------------------------------

definePageMeta({
  validate: (route) => {
    const raw = route.params.league
    const key = Array.isArray(raw) ? raw[0] : raw
    return typeof key === 'string' && LEAGUES.some((l) => l.key === key)
  },
  middleware: [
    function aflStaysAtRoot(to) {
      const raw = to.params.league
      const key = Array.isArray(raw) ? raw[0] : raw
      if (key === 'afl') {
        return navigateTo('/', { replace: true })
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
// Active-league sync (bundle §3 redirect-middleware notes): visiting
// /{league} must move the header dropdown with the route. Client-only —
// the league store is module-scoped, so mutating it during prerender
// would leak one league's selection into every statically generated page.
// ---------------------------------------------------------------------------
const { setActiveLeague } = useActiveLeague()
if (import.meta.client && leagueKey.value) {
  setActiveLeague(leagueKey.value)
}
watch(leagueKey, (key) => {
  if (import.meta.client && key) setActiveLeague(key)
})

// ---------------------------------------------------------------------------
// Data — route-driven league events. LEAGUE-ROUTES (subtask 07,
// 2026-09-30): the league key is REQUIRED and comes straight from the
// URL — the legacy no-arg global-selector mode was removed together
// with the home page's league branch, so this is the only call site.
//
// LEAGUE-ROUTES (2026-09-30, code review): the composable's fetch is
// an awaited useAsyncData, so it runs AT GENERATE TIME and inlines the
// fixtures payload into the prerendered HTML — the watch+refs wiring
// previously baked hero+spinner (an empty shell) into every static
// league page, the exact SEO-C1 failure pages/index.vue documents as
// fixed for the AFL home.
// ---------------------------------------------------------------------------
const {
  seasonEvents,
  roundId,
  seasonLabel,
  roundEvents,
  pending,
  error,
  unavailable,
  refresh,
} = await useLeagueEvents(leagueKey)

// ---------------------------------------------------------------------------
// FCL-12 forward tips: the round's stored league picks (bulk endpoint,
// NO AI explanations). Awaited AFTER the fixtures fetch so `roundId` is
// already resolved and the payload inlines at generate time; a null or
// failed payload degrades to the pre-FCL status labels (non-fatal).
// ---------------------------------------------------------------------------
const { tipsBySlug } = await useLeagueRoundTips(leagueKey, roundId)

// ---------------------------------------------------------------------------
// FCL-13 (2026-10-13, user request): the AFL home's user-selectable
// heuristic, mirrored for every league. The tablist lists the league's
// BASE model set + the derived trio (no AFL-only `yolo`); the selected
// heuristic drives which stored pick each upcoming card shows — no
// refetch, the round payload already carries every heuristic's pick.
// Restore mirrors the AFL home's localStorage read (same key), client-
// only and validated against the league-legal list.
// ---------------------------------------------------------------------------
const heuristicOptions = computed(() =>
  getLeagueHeuristicOptions(leagueKey.value ?? 'afl'),
)
const selectedHeuristic = ref<string>('best_bet')
/** The selected tab's display label (the card's tip badge). */
const heuristicLabel = computed(
  () =>
    heuristicOptions.value.find((h) => h.value === selectedHeuristic.value)
      ?.label ?? 'Model tip',
)
onMounted(() => {
  const stored = localStorage.getItem('selected-heuristic')
  const legal = heuristicOptions.value.some((h) => h.value === stored)
  if (stored && legal && stored !== selectedHeuristic.value) {
    selectedHeuristic.value = stored
  }
})

/**
 * The card's tip for an upcoming fixture under the SELECTED heuristic —
 * the AFL card shape (FCL-14): the stored pick's side name, confidence
 * (0..1) and margin (points).  Null when no stored tip exists for that
 * heuristic — the card shows the AFL's "No tip available" state.
 */
const pickFor = (
  ev: SportEvent,
): {
  name: string
  confidence: number | null
  margin: number | null
} | null => {
  const pick = tipsBySlug.value[ev.slug]?.[selectedHeuristic.value]
  if (!pick || pick.picked === undefined) return null
  return {
    name: pick.picked === null ? 'Draw' : pick.picked,
    confidence: pick.confidence ?? null,
    margin: pick.margin ?? null,
  }
}

// Per-league presentation config resolved from the ROUTE (not the
// global activeConfig): the prerender bakes every league page in one
// process, so a globally-scoped selection would leak another league's
// labels into the static HTML.
const leagueConfig = computed(() => getLeagueConfig(leagueKey.value ?? 'afl'))

// ---------------------------------------------------------------------------
// End-of-season state (subtask 01 helpers): the celebration/strip switch
// is derived from the FULL season payload + the displayed round.
// ---------------------------------------------------------------------------
const seasonState = computed(() => deriveSeasonState(seasonEvents.value, roundId.value))
const isSeasonComplete = computed(() => seasonState.value === 'season_complete')

// Round strip: 'GF • {season}' for the final round (upcoming or done),
// 'R{n} • {season}' for every regular round.
const roundStrip = computed(() => {
  if (roundId.value === null) return ''
  const season = seasonLabel.value ?? ''
  return seasonState.value === 'regular'
    ? `R${roundId.value} • ${season}`
    : `GF • ${season}`
})

// Premier for the celebration: winner of the latest-dated completed event.
const premier = computed(() => derivePremier(seasonEvents.value))

// OffSeasonCelebration renders the season as a number (AFL contract);
// the league season label is the same four-digit year as a string.
const seasonNumber = computed<number | null>(() => {
  const parsed = Number.parseInt(seasonLabel.value ?? '', 10)
  return Number.isFinite(parsed) ? parsed : null
})

// Confetti palette: both finalists' club colours from the curated
// league palettes (primary + secondary per club). Null when no palette
// resolves — ConfettiEffect then falls back to its celebratory default.
const confettiColors = computed<string[] | null>(() => {
  const league = leagueKey.value
  if (!league) return null
  const colors: string[] = []
  for (const ev of roundEvents.value) {
    for (const p of ev.participants) {
      const club = leagueColorFor(league, p.participant_name)
      if (club && !colors.includes(club.primary)) {
        colors.push(club.primary, club.secondary)
      }
    }
  }
  return colors.length > 0 ? colors : null
})

// ---------------------------------------------------------------------------
// Card helpers (moved across from the home view's league branch).
// ---------------------------------------------------------------------------
const sideOf = (ev: SportEvent, side: string) =>
  ev.participants.find((p) => p.side === side)
const sideName = (ev: SportEvent, side: string): string =>
  sideOf(ev, side)?.participant_name ?? 'TBD'
const sideScore = (ev: SportEvent, side: string): number | null =>
  sideOf(ev, side)?.score ?? null
const sideIsWinner = (ev: SportEvent, side: string): boolean =>
  sideOf(ev, side)?.is_winner === true

// Identity: the extended logoFor resolves AFL club files as before and
// generates initials badges for state-league clubs (subtask 02).
const { logoFor } = useTeamIdentity()
const { getTeamDisplayName } = useTeamLogos()
const { formatDate } = useFormatters()

const displayName = (name: string): string => getTeamDisplayName(name)

const sideLogo = (ev: SportEvent, side: string): string | null => {
  const name = sideName(ev, side)
  return logoFor(name, leagueKey.value)
}

// ---------------------------------------------------------------------------
// SEO — same pattern as the AFL home: state-aware getters for the title
// and a canonical <link> through useHead (useSeoMeta alone cannot set
// canonicals). titleTemplate (nuxt.config) appends the site name.
// ---------------------------------------------------------------------------
const siteUrl = useRuntimeConfig().public.siteUrl as string
const leagueName = computed(() => leagueConfig.value.displayName)

useSeoMeta({
  title: () => `${leagueName.value} Tips & Fixtures`,
  description: () =>
    `Every ${leagueName.value} game of the current round — fixtures, results and scores, updated automatically.`,
  ogTitle: () => `${leagueName.value} Tips & Fixtures`,
  ogDescription: () =>
    `Every ${leagueName.value} game of the current round — fixtures, results and scores.`,
  twitterTitle: () => `${leagueName.value} Tips & Fixtures`,
  twitterDescription: () =>
    `Every ${leagueName.value} game of the current round — fixtures, results and scores.`,
})

useHead({
  link: [
    { rel: 'canonical', href: () => `${siteUrl}/${leagueKey.value ?? ''}` },
  ],
})
</script>

<style scoped>
/* Hero — byte-identical spacing/copy to the AFL home's hero. */
.hero {
  /* DESIGN-FIX (rhythm): matches index.vue — no dead zone between the
     hero and the round strip. */
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

/* Round Display */
.round-display {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 1rem 1.5rem;
  border: 1px solid var(--color-border);
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
}

.round-label {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.round-value {
  font-size: 1.25rem;
  font-weight: 800;
}

.game-count {
  font-size: 0.8125rem;
  color: var(--color-muted);
}

/* FCL-13 heuristic selector — byte-identical monochrome tablist to the
   AFL home's .heuristic-selector (inverted active tab). */
.heuristic-selector {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 1.5rem;
}

.heuristic-btn {
  font-size: 0.8125rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 0.5rem 0.875rem;
  min-height: 44px;
  border: 2px solid var(--color-border);
  background: none;
  color: var(--color-muted);
  cursor: pointer;
  transition: all 0.2s ease;
}

.heuristic-btn:hover {
  color: var(--color-text);
  border-color: var(--color-text);
}

.heuristic-btn.active {
  background: var(--color-text);
  color: var(--color-bg);
  border-color: var(--color-text);
}

.heuristic-btn:focus-visible {
  outline: 2px solid var(--color-text);
  outline-offset: 2px;
}

/* AFL home parity: keyed fade between heuristic selections. */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.loading, .error, .empty {
  text-align: center;
  padding: 3rem 1.5rem;
}

.empty-hint {
  margin-top: 0.5rem;
  font-size: 0.875rem;
  color: var(--color-muted);
}

/* Games Grid */
.games-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.5rem;
}

.game-card-link {
  display: block;
  cursor: pointer;
  transition: all 0.2s ease-in-out;
  text-decoration: none;
  color: var(--color-text);
  /* Touch-target floor: the whole card is the link. */
  min-height: 44px;
}

.game-card-link:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);
}

.game-card-link:focus-visible {
  outline: 2px solid var(--color-text);
  outline-offset: 2px;
}

/* Match Info */
.game-card {
  border: 1px solid var(--color-border);
  padding: 1.5rem;
  height: 100%;
  /* Equal-height cards — the result block fills the card. */
  display: flex;
  flex-direction: column;
}

.match-info {
  margin-bottom: 1.5rem;
  padding-bottom: 1.25rem;
  border-bottom: 1px solid var(--color-border);
}

.teams {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.team {
  flex: 1;
  font-size: 1.125rem;
  font-weight: 700;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.375rem;
}

.team.home {
  text-align: center;
  justify-content: center;
}

.team.away {
  text-align: center;
  justify-content: center;
}

.team-name {
  font-size: 0.8125rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  line-height: 1.2;
}

.team-logo {
  width: 40px;
  height: 40px;
  object-fit: contain;
}

.vs {
  font-size: 0.8125rem;
  font-weight: 700;
  padding: 0 0.75rem;
}

.match-details {
  display: flex;
  justify-content: space-between;
  font-size: 0.8125rem;
  color: var(--color-muted);
  flex-wrap: wrap;
  gap: 0.5rem;
}

.no-tip {
  text-align: center;
  padding: 1.5rem;
  color: var(--color-muted);
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

/* FCL-14: the AFL card's tip block — byte-identical classes and
   treatment to the AFL home's .tip-info (badge / confidence / winner /
   margin), so a league card is indistinguishable from an AFL card. */
.tip-info {
  background: var(--color-hover);
  padding: 1.25rem;
  border-radius: 4px;
  /* Fill remaining card height so sibling cards align */
  flex: 1;
}

.tip-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.875rem;
  padding-bottom: 0.875rem;
  border-bottom: 1px solid var(--color-border);
}

.heuristic-badge {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.confidence {
  font-size: 0.8125rem;
  font-weight: 700;
}

.tip-body h3 {
  font-size: 1.25rem;
  margin-bottom: 0.5rem;
}

.tip-body .margin {
  font-size: 0.8125rem;
  margin: 0;
}

/* League fixture results — winner emphasised without colour, matching
   the monochrome card language of the home view. */
.league-result {
  background: var(--color-hover);
  padding: 1.25rem;
  border-radius: 4px;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.result-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 1rem;
}

.result-team {
  font-size: 0.9375rem;
  font-weight: 600;
}

.result-team.winner {
  font-weight: 800;
}

.result-score {
  font-size: 1rem;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}

/* Mobile styles */
@media (max-width: 640px) {
  .hero {
    padding: 2.5rem 1rem 1.75rem;
  }

  .hero h1 {
    margin-bottom: 1rem;
  }

  .hero p {
    font-size: 1rem;
  }

  .section {
    padding: 2rem 1rem;
  }

  .round-display {
    padding: 0.875rem 1rem;
    gap: 0.5rem;
  }

  .round-value {
    font-size: 1.125rem;
  }

  .games-grid {
    grid-template-columns: 1fr;
    gap: 1rem;
  }

  .game-card {
    padding: 1.25rem;
  }

  .team {
    font-size: 1rem;
  }

  .vs {
    padding: 0 0.5rem;
    font-size: 0.75rem;
  }

  .loading, .error, .empty {
    padding: 2rem 1rem;
  }
}

/* Tablet styles */
@media (min-width: 641px) and (max-width: 1024px) {
  .hero {
    padding: 5rem 1.5rem;
  }

  .games-grid {
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
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

  .games-grid {
    grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
    gap: 2rem;
  }

  .game-card {
    padding: 2rem;
  }

  .team {
    font-size: 1.25rem;
  }

  .vs {
    font-size: 0.875rem;
    padding: 0 1rem;
  }

  .match-details {
    font-size: 0.875rem;
  }

  .round-display {
    padding: 1.5rem;
    gap: 1rem;
  }

  .round-value {
    font-size: 1.5rem;
  }

  .game-count {
    font-size: 0.875rem;
  }

  .round-label {
    font-size: 0.75rem;
  }
}
</style>
