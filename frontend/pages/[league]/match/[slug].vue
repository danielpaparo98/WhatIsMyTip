<template>
  <div class="match-detail-page">
    <!-- LEAGUE-ROUTES (2026-09-30, user request): the /{league}/match/{slug}
         event detail page.  Presentation mirrors /game/[slug].vue's header
         (back link, round/season/status strip, two teams with badges +
         scores when completed, venue/date/time meta grid) — but the tips /
         model-prediction / weather sections are deliberately ABSENT: state
         leagues have no tipping data, so there is nothing honest to render
         there.  Winner emphasis follows the league home's result rows
         (bold-winner, monochrome) instead of the AFL page's accent-free
         equality between sides. -->
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
    </div>
  </div>
</template>

<script setup lang="ts">
// LEAGUE-ROUTES (2026-09-30, user request): league event detail page.
// Data comes from the ADR 0001 read API (getEvent, useApi.ts); the pure
// route rules (slug shape, league-key registry, cross-league ownership)
// live in composables/useLeagueMatchRoute.ts so vitest can exercise them
// without mounting this SFC.
import type { EventDetailResponse, SportEvent } from '~/composables/useApi'
import {
  validateLeagueMatchRoute,
  eventMatchesLeague,
  isValidEventSlug,
} from '~/composables/useLeagueMatchRoute'
import { getLeagueConfig } from '~/composables/useSportConfig'

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
const { getEvent } = useApi()
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

// H-5-revised pattern (mirrors /game/[slug].vue): the useAsyncData key is
// REACTIVE and includes league + slug — every match gets its own cache
// slot, and a key change triggers the fetch automatically (a stale
// previous match can never render under a new URL).  dedupe: 'cancel'
// drops superseded in-flight fetches on rapid navigation.
const {
  data: event,
  pending: loading,
  error: fetchError,
  refresh: refetch,
} = await useAsyncData<EventDetailResponse | null>(
  computed(() => `league-match-${league.value}-${slug.value}`),
  async () => {
    // Same pre-flight guard as the game page: reject slugs the backend
    // could never return before spending an API round-trip.
    if (!isValidEventSlug(slug.value)) {
      throw new Error('Invalid event slug')
    }
    const detail = await getEvent(slug.value)
    // Cross-league slug-probing guard: /api/events/{slug} resolves ANY
    // league's event, so a WAFL URL must not render a SANFL payload.
    // A mismatch is a genuine 404 (wrong URL), not a transient error —
    // showError renders the global 404 page with the proper status.
    if (!eventMatchesLeague(detail, league.value)) {
      showError(createError({ statusCode: 404, statusMessage: 'Match not found' }))
      return null
    }
    return detail
  },
  {
    dedupe: 'cancel',
  },
)

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
