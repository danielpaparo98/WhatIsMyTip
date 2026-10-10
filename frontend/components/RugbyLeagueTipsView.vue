<template>
  <!-- =====================================================================
       NRL-TIPS (nrl-expansion-09): the shared tips view behind the three
       first-class rugby-league routes (pages/nrl, pages/nrlw, pages/origin
       — static segments outrank pages/[league], so those URLs never reach
       the fixtures-only league page, which stays byte-identical for the
       state leagues).

       Presentation follows the established league-page conventions:
       always-on hero, round strip, clickable cards with generated club
       badges, and the end-of-season celebration — PLUS the tipping
       surface the rugby-league competitions uniquely have: every page
       names its reduced model set and every upcoming card carries the
       model-tips block, degrading to a pending note until picks land
       (never a crash on missing tips).

       Label/wording contract: heuristic/model labels, the contest noun
       and the stage noun flow from getLeagueConfig(league) — the
       rugby-league SportConfig — never from literals in this file.
       ===================================================================== -->
  <section class="hero">
    <h1>AI-Powered<br>{{ leagueConfig.displayName }} Tipping</h1>
    <p>Smart heuristics. Clear explanations. Better tips.</p>
  </section>

  <div v-if="pending" class="loading" role="status" aria-live="polite">
    <div class="spinner"></div>
  </div>

  <div v-else-if="error" class="error" role="status" aria-live="polite">
    <p>{{ error }}</p>
    <button @click="retry" class="btn">Retry</button>
  </div>

  <!-- League not synced yet (no competition/season in /api/sports), or a
       non-rugby-league key reached this view (caller bug — degrade, never
       present one competition's models under another's name). -->
  <div v-else-if="showUnavailable" class="empty" role="status" aria-live="polite">
    <p>No {{ leagueConfig.displayName }} fixtures have been synced yet.</p>
    <p class="empty-hint">Fixtures land automatically once the league's data source is live.</p>
  </div>

  <template v-else>
    <!-- End of season: the premier celebration carries the same
         ALWAYS-HERO convention as the AFL post-season state; the final
         round's results stay below so the page keeps its substance. -->
    <template v-if="isSeasonComplete">
      <OffSeasonCelebration
        :premier="premier"
        :season="seasonNumber"
        :league="league"
      />
    </template>

    <section class="section">
      <!-- Round strip: "{stageNoun} {n} • {season}" for regular rounds,
           "GF • {season}" for the season's final round (upcoming or
           played). Wording comes from formatRoundStrip + the config. -->
      <div v-if="roundId !== null" class="round-display">
        <span class="round-label">{{ leagueConfig.displayName }}</span>
        <span class="round-value">{{ roundStrip }}</span>
        <span class="game-count">{{ roundEvents.length }} {{ leagueConfig.contestNoun }}s</span>
      </div>

      <!-- The reduced tipping model set (nrl-expansion-08): order and
           labels resolve straight from the league's SportConfig maps. -->
      <div class="model-strip">
        <span class="model-strip-label">Tipped by</span>
        <span
          v-for="model in tipModelList"
          :key="model.key"
          class="model-chip"
        >
          {{ model.label }}
        </span>
      </div>

      <div v-if="roundEvents.length === 0" class="empty" role="status" aria-live="polite">
        <p>No fixtures found for this round yet.</p>
      </div>
      <div v-else class="games-grid">
        <NuxtLink
          v-for="ev in roundEvents"
          :key="ev.slug"
          :to="`/${league}/match/${ev.slug}`"
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

            <!-- Played: result rows with the winner emphasised (bold,
                 monochrome — the league-page card language). -->
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

            <!-- Upcoming: the model-tips block. The competition's models
                 are named above; per-match picks are generated after each
                 data sync, so until then the block degrades to the pending
                 note — a missing tip is an expected state, never a crash. -->
            <div v-else class="tips-block">
              <span class="tips-eyebrow">Model tips</span>
              <p class="tips-pending">Picks are generated automatically after each data sync.</p>
            </div>
          </article>
        </NuxtLink>
      </div>
    </section>
  </template>

  <!-- Once the season is complete, confetti fires in BOTH finalists' club
       colours (the curated rugby-league palettes). The `active` prop opts
       this view out of ConfettiEffect's AFL grand-final gate — the AFL
       home keeps driving it from latestRound, untouched. -->
  <ConfettiEffect
    v-if="isSeasonComplete"
    :colors="confettiColors ?? undefined"
    active
  />
</template>

<script setup lang="ts">
// NRL-TIPS (nrl-expansion-09): shared view for the static rugby-league
// tips routes. Data comes from the same route-driven useLeagueEvents
// pipeline the league pages use (awaited useAsyncData — fixtures inline
// into the prerendered HTML), presentation config from getLeagueConfig
// (which resolves the rugby-league SportConfig for these keys), and club
// identity from the committed colour palettes via useTeamIdentity —
// colour-initials badges when no logo asset exists.
import type { SportEvent } from '~/composables/useApi'
import { getLeagueConfig } from '~/composables/useSportConfig'
import { useLeagueEvents } from '~/composables/useLeagueEvents'
import { derivePremier, deriveSeasonState } from '~/composables/useSeasonState'
import { leagueColorFor } from '~/composables/useLeagueColors'
import {
  formatRoundStrip,
  isRugbyLeagueLeague,
  tipModels,
} from '~/composables/useRugbyLeagueTips'

/** The route key this instance presents: 'nrl' | 'nrlw' | 'origin'. */
const props = defineProps<{ league: string }>()

const leagueKey = computed(() => props.league)

// Per-competition presentation config — the single source for display
// names, nouns, and every heuristic/model label below.
const leagueConfig = computed(() => getLeagueConfig(props.league))

// Misuse guard: this view is exclusively for the rugby-league
// competitions; anything else renders the unavailable state.
const isRugbyLeague = computed(() => isRugbyLeagueLeague(props.league))

// ---------------------------------------------------------------------------
// Data — the awaited fetch runs AT GENERATE TIME and inlines the season
// payload into the prerendered HTML (the league pages' SEO-C1 contract);
// a client-side refresh after Nuxt-ready keeps hydrated visits fresh.
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

const showUnavailable = computed(() => unavailable.value || !isRugbyLeague.value)

// Re-expose a retry for the error state template (the match page's
// pattern — a plain wrapper keeps the button handler void-typed).
const retry = () => refresh()

// ---------------------------------------------------------------------------
// End-of-season state: the celebration/strip switch is derived from the
// FULL season payload + the displayed round (same helpers as the league
// pages — pure, and unit-tested in season-state.test.ts).
// ---------------------------------------------------------------------------
const seasonState = computed(() => deriveSeasonState(seasonEvents.value, roundId.value))
const isSeasonComplete = computed(() => seasonState.value === 'season_complete')

const roundStrip = computed(() =>
  formatRoundStrip(roundId.value, seasonLabel.value, seasonState.value, leagueConfig.value.stageNoun),
)

// Premier for the celebration: winner of the latest-dated completed event.
const premier = computed(() => derivePremier(seasonEvents.value))

// OffSeasonCelebration renders the season as a number; the league season
// label is the same four-digit year as a string.
const seasonNumber = computed<number | null>(() => {
  const parsed = Number.parseInt(seasonLabel.value ?? '', 10)
  return Number.isFinite(parsed) ? parsed : null
})

// The reduced model set, in the config's display order, labels resolved
// through the config's map (raw key as the visible fallback).
const tipModelList = computed(() =>
  tipModels(leagueConfig.value.heuristicOrder, leagueConfig.value.heuristicLabels),
)

// Confetti palette: both finalists' club colours from the curated
// league palettes (primary + secondary per club). Null when no palette
// resolves — ConfettiEffect then falls back to its celebratory default.
const confettiColors = computed<string[] | null>(() => {
  const colors: string[] = []
  for (const ev of roundEvents.value) {
    for (const p of ev.participants) {
      const club = leagueColorFor(props.league, p.participant_name)
      if (club && !colors.includes(club.primary)) {
        colors.push(club.primary, club.secondary)
      }
    }
  }
  return colors.length > 0 ? colors : null
})

// ---------------------------------------------------------------------------
// Card helpers (the league pages' side helpers, prop-scoped).
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
// generates colour-initials badges for the rugby-league clubs.
const { logoFor } = useTeamIdentity()
const { getTeamDisplayName } = useTeamLogos()
const { formatDate } = useFormatters()

const displayName = (name: string): string => getTeamDisplayName(name)

const sideLogo = (ev: SportEvent, side: string): string => {
  const name = sideName(ev, side)
  // logoFor always resolves to a URL (the initials-badge/placeholder
  // chain never returns null in practice); the coalescing keeps the
  // <img src> binding string-typed (the match page's exact pattern).
  return logoFor(name, props.league) ?? ''
}

// ---------------------------------------------------------------------------
// Active-league sync: visiting the route moves the header dropdown with
// it. Client-only — the league store is module-scoped, so mutating it
// during prerender would leak one league's selection into every
// statically generated page.
// ---------------------------------------------------------------------------
const { setActiveLeague } = useActiveLeague()
if (import.meta.client && props.league) {
  setActiveLeague(props.league)
}

// ---------------------------------------------------------------------------
// SEO — same pattern as the league pages: state-aware getters for the
// title and a canonical <link> through useHead (useSeoMeta alone cannot
// set canonicals). titleTemplate (nuxt.config) appends the site name.
// ---------------------------------------------------------------------------
const siteUrl = useRuntimeConfig().public.siteUrl as string
const leagueName = computed(() => leagueConfig.value.displayName)

useSeoMeta({
  title: () => `${leagueName.value} Tips & Fixtures`,
  description: () =>
    `Every ${leagueName.value} ${leagueConfig.value.contestNoun.toLowerCase()} of the current round — fixtures, results and model tips, updated automatically.`,
  ogTitle: () => `${leagueName.value} Tips & Fixtures`,
  ogDescription: () =>
    `Every ${leagueName.value} ${leagueConfig.value.contestNoun.toLowerCase()} of the current round — fixtures, results and model tips.`,
  twitterTitle: () => `${leagueName.value} Tips & Fixtures`,
  twitterDescription: () =>
    `Every ${leagueName.value} ${leagueConfig.value.contestNoun.toLowerCase()} of the current round — fixtures, results and model tips.`,
})

useHead({
  link: [
    { rel: 'canonical', href: () => `${siteUrl}/${leagueKey.value}` },
  ],
})
</script>

<style scoped>
/* Hero — the always-on hero convention (identical rhythm to the league
   pages; the headline names THIS competition via the config). */
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

/* Model strip — the competition's reduced tipping model set, named once
   per page. Chips are outlined, uppercase, monochrome. */
.model-strip {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.875rem 1rem;
  border: 1px solid var(--color-border);
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
}

.model-strip-label {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.model-chip {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 0.25rem 0.625rem;
  border: 1px solid var(--color-border);
  color: var(--color-text);
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
  /* Equal-height cards — the result/tips block fills the card. */
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

/* Fixture results — winner emphasised without colour, matching the
   monochrome card language of the league pages. */
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

/* Model-tips block on upcoming cards — the pending degradation for the
   rounds whose picks have not landed yet (an expected state, never a
   crash). Same boxed weight as the result block. */
.tips-block {
  background: var(--color-hover);
  padding: 1.25rem;
  border-radius: 4px;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  text-align: center;
}

.tips-eyebrow {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.tips-pending {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-muted);
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

  .model-strip {
    padding: 0.75rem 0.875rem;
    gap: 0.375rem;
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
