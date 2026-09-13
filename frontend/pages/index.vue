<template>
  <section class="hero">
        <h1>AI-Powered<br>Footy Tipping</h1>
        <p>Smart heuristics. Clear explanations. Better tips.</p>
      </section>

      <section class="section">
        <!-- Round Display -->
        <div v-if="round" class="round-display">
          <span class="round-label">{{ round.is_current_year ? 'Current Round' : 'Latest Available' }}</span>
          <span class="round-value">R{{ round.round_id }} • {{ round.season }}</span>
          <span class="game-count">{{ round.game_count }} Games</span>
        </div>

        <!-- Data Warning -->
        <div v-if="round && !round.is_current_year" class="data-warning">
          <p>
            <strong>No data available for {{ new Date().getFullYear() }}.</strong>
            Showing historical data from {{ round.season }}.
          </p>
        </div>

        <!-- Heuristic Selector -->
        <div class="heuristic-selector" role="tablist" aria-label="Heuristic filter">
          <button
            v-for="h in heuristics"
            :key="h.value"
            role="tab"
            :aria-selected="selectedHeuristic === h.value"
            @click="selectedHeuristic = h.value"
            :class="['heuristic-btn', { active: selectedHeuristic === h.value }]"
          >
            {{ h.label }}
          </button>
        </div>

        <!-- Games with Tips -->
        <Transition name="fade" mode="out-in">
          <div :key="selectedHeuristic">
            <div v-if="loading" class="loading" role="status" aria-live="polite">
              <div class="spinner"></div>
            </div>
            <div v-else-if="error" class="error" role="status" aria-live="polite">
              <p>{{ error }}</p>
              <button @click="refreshAll" class="btn">Retry</button>
            </div>
            <div v-else-if="gamesWithTips.length === 0" class="empty" role="status" aria-live="polite">
              <p>No tips available for this round yet.</p>
              <p class="empty-hint">Tips are generated automatically after each round's data sync.</p>
            </div>
            <div v-else class="games-grid">
            <NuxtLink
              v-for="game in gamesWithTips"
              :key="game.id"
              :to="`/game/${game.slug}`"
              class="game-card-link"
            >
              <div class="game-card">
                <!-- Match Info -->
              <div class="match-info">
                <div class="teams">
                  <div class="team home">
                    <img :src="getLogoUrl(game.home_team ?? 'TBD')" :alt="`${game.home_team ?? 'TBD'} logo`" class="team-logo" loading="lazy" decoding="async" width="40" height="40" />
                  </div>
                  <span class="vs">VS</span>
                  <div class="team away">
                    <img :src="getLogoUrl(game.away_team ?? 'TBD')" :alt="`${game.away_team ?? 'TBD'} logo`" class="team-logo" loading="lazy" decoding="async" width="40" height="40" />
                  </div>
                </div>
                <div class="match-details">
                  <span class="venue">{{ game.venue ?? 'TBD' }}</span>
                  <span class="date">{{ game.date ? formatDate(game.date) : 'TBD' }}</span>
                </div>
              </div>

              <!-- Tip Info -->
              <div v-if="game.tip" class="tip-info">
                <div class="tip-header">
                  <span class="heuristic-badge">{{ formatHeuristic(game.tip.heuristic) }}</span>
                  <span class="confidence">{{ Math.round(game.tip.confidence * 100) }}%</span>
                </div>
                <div class="tip-body">
                  <h3>{{ game.tip.selected_team }}</h3>
                  <p class="margin">Margin: {{ game.tip.margin }} pts</p>
                </div>
                <p v-if="game.tip.explanation" class="explanation">{{ game.tip.explanation }}</p>
              </div>
              <div v-else class="no-tip">
                <p>No tip available</p>
              </div>

              </div>
            </NuxtLink>
          </div>
        </div>
        </Transition>
  </section>
</template>

<script setup lang="ts">
import type { GameWithTip, GamesWithTipsResponse, LatestRoundResponse } from '~/composables/useApi'
import { HEURISTIC_ORDER } from '~/composables/useFormatters'
import { AUTO_REFRESH_MS, useLatestRound } from '~/composables/useLatestRound'
const api = useApi()
const { getLogoUrl } = useTeamLogos()
const { formatHeuristic, formatDate: formatDateUtil } = useFormatters()

// ---------------------------------------------------------------------------
// UI state (declared first — the fetch watcher below reads it)
// ---------------------------------------------------------------------------
const selectedHeuristic = ref<string>('weighted_tip')

const heuristics = HEURISTIC_ORDER.map(value => ({
  value,
  label: formatHeuristic(value)
}))

const formatDate = formatDateUtil

// ---------------------------------------------------------------------------
// Data fetching — SEO-C1 / H-3 / M-4 (2026-09 review)
//
// This page used to fetch everything in onMounted (client-only), so the
// prerendered HTML shipped an empty shell to crawlers and social
// previews.  Fetching now happens in <setup> via useAsyncData: it runs
// at generate time (data is inlined into the prerendered HTML AND the
// Nuxt payload, so hydration needs no refetch), and re-runs reactively
// when the heuristic or round changes.
// ---------------------------------------------------------------------------
const { data: round, refresh: refreshRound } = await useAsyncData<LatestRoundResponse>(
  'latest-round',
  () => api.getLatestRound(),
)

// Publish the round into the shared store so passive consumers
// (OffSeasonBanner, ConfettiEffect) hydrate without fetching.
const { setLatestRound } = useLatestRound()
setLatestRound(round.value)

// Reactive season/round locator (falls back to current year / round 1).
const seasonRound = computed(() => ({
  season: round.value?.season ?? new Date().getFullYear(),
  round: round.value?.round_id ?? 1,
}))

const { data: gamesData, pending: loading, error: gamesError, refresh: refreshGames } =
  await useAsyncData<GamesWithTipsResponse>(
    'games-with-tips',
    () => api.getGamesWithTips(seasonRound.value.season, seasonRound.value.round, selectedHeuristic.value),
    {
      // FIX H-3: `watch` + `dedupe: 'cancel'` means a rapid heuristic
      // switch cancels the in-flight request instead of letting a slow
      // stale response overwrite the newer selection (previously
      // last-RESOLVED won → wrong tips under the wrong tab).
      watch: [selectedHeuristic, seasonRound],
      dedupe: 'cancel',
    },
  )

const gamesWithTips = computed<GameWithTip[]>(() => gamesData.value?.games ?? [])

const error = computed<string | null>(() => {
  if (!gamesError.value) return null
  return 'Failed to load tips'
})

const refreshAll = async () => {
  await refreshRound()
  setLatestRound(round.value)
  await refreshGames()
}

// ---------------------------------------------------------------------------
// Client-side behaviour
// ---------------------------------------------------------------------------

// Restore the persisted heuristic preference (client-only).  Setting
// the ref triggers the useAsyncData watcher — no manual double fetch.
onMounted(() => {
  const stored = localStorage.getItem('selected-heuristic')
  if (stored && HEURISTIC_ORDER.includes(stored) && stored !== selectedHeuristic.value) {
    selectedHeuristic.value = stored
  }
})

// Auto-refresh: ONE poller, owned by this page (previously the page,
// OffSeasonBanner and ConfettiEffect each polled independently).  A
// round change flows through `seasonRound` and re-triggers the games
// fetch reactively.
let autoRefreshTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  autoRefreshTimer = setInterval(async () => {
    if (document.visibilityState !== 'visible') return
    try {
      await refreshRound()
      setLatestRound(round.value)
    } catch (e) {
      if (import.meta.dev) console.error('Auto-refresh failed:', e)
    }
  }, AUTO_REFRESH_MS)
})
onUnmounted(() => {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer)
    autoRefreshTimer = null
  }
})

// ---------------------------------------------------------------------------
// SEO — FX-05 / FX-20 / H-2 (2026-09 review)
// ---------------------------------------------------------------------------
// `useSeoMeta({ canonical })` was NOT a supported key — it silently
// rendered nothing.  Canonicals are <link> elements and go through
// useHead, using the siteUrl runtime config.
const siteUrl = useRuntimeConfig().public.siteUrl as string

useHead({
  title: 'AFL Tips & Predictions',
  link: [
    { rel: 'canonical', href: siteUrl }
  ],
  meta: [
    { name: 'description', content: 'Get AI-powered AFL tips and predictions for the current round. Expert footy tipping advice with smart heuristics, betting tips, and round predictions backed by machine learning models.' },
    { name: 'keywords', content: 'AFL tips, AFL predictions, AFL betting tips, AFL footy tips, AFL round predictions, AFL betting advice, footy tipping, AFL betting' },
    { property: 'og:type', content: 'website' },
    { property: 'og:title', content: 'AFL Tips & Predictions | AI-Powered Footy Tipping' },
    { property: 'og:description', content: 'Get AI-powered AFL tips and predictions for the current round. Expert footy tipping advice with smart heuristics.' },
    { property: 'og:url', content: siteUrl },
    { name: 'twitter:title', content: 'AFL Tips & Predictions | AI-Powered Footy Tipping' },
    { name: 'twitter:description', content: 'Get AI-powered AFL tips and predictions for the current round. Expert footy tipping advice with smart heuristics.' }
  ],
  script: [
    {
      type: 'application/ld+json',
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'WebPage',
        name: 'AFL Tips & Predictions',
        description: 'Get AI-powered AFL tips and predictions for the current round. Expert footy tipping advice with smart heuristics.',
        url: siteUrl,
        mainEntity: {
          '@type': 'SportsEvent',
          sport: 'Australian Rules Football',
          name: 'AFL Tips'
        }
      })
    }
  ]
})
</script>

<style scoped>
.hero {
  padding: 4rem 1.5rem;
  text-align: center;
}

.hero h1 {
  font-size: clamp(2rem, 8vw, 6rem);
  line-height: 1.05;
  margin-bottom: 1.5rem;
}

.hero p {
  font-size: 1.125rem;
  max-width: 600px;
  margin: 0 auto;
}

.section {
  padding: 3rem 1.5rem;
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

/* Data Warning */
.data-warning {
  padding: 0.875rem 1.25rem;
  background: rgba(255, 193, 7, 0.1);
  border: 1px solid rgba(255, 193, 7, 0.3);
  border-radius: 8px;
  margin-bottom: 1.5rem;
  text-align: center;
}

.data-warning p {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text);
}

.data-warning strong {
  color: #f59e0b;
}

/* Heuristic Selector */
.heuristic-selector {
  display: flex;
  justify-content: center;
  gap: 0.5rem;
  margin-bottom: 2rem;
  flex-wrap: wrap;
}

.heuristic-btn {
  padding: 0.625rem 1.25rem;
  font-size: 0.8125rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border: 2px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text);
  cursor: pointer;
  transition: all 0.2s ease;
  min-height: 44px;
  min-width: 44px;
}

.heuristic-btn:hover {
  border-color: var(--color-text);
}

.heuristic-btn.active {
  background: var(--color-text);
  color: var(--color-bg);
  border-color: var(--color-text);
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
}

.game-card-link:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);
}

.game-card {
  border: 1px solid var(--color-border);
  padding: 1.5rem;
  height: 100%;
}

/* Match Info */
.match-info {
  margin-bottom: 1.5rem;
  padding-bottom: 1.25rem;
  border-bottom: 1px solid var(--color-border);
}

.teams {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.team {
  flex: 1;
  font-size: 1.125rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.team.home {
  text-align: right;
  justify-content: flex-end;
}

.team.away {
  text-align: left;
  justify-content: flex-start;
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

/* Tip Info */
.tip-info {
  background: var(--color-hover);
  padding: 1.25rem;
  border-radius: 4px;
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

.explanation {
  margin-top: 0.875rem;
  font-size: 0.875rem;
  line-height: 1.5;
}

.no-tip {
  text-align: center;
  padding: 1.5rem;
  color: var(--color-muted);
}

/* Mobile styles */
@media (max-width: 640px) {
  .hero {
    padding: 3rem 1rem;
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

  .heuristic-selector {
    margin-bottom: 1.5rem;
  }

  .heuristic-btn {
    padding: 0.5rem 1rem;
    font-size: 0.75rem;
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

  .tip-body h3 {
    font-size: 1.125rem;
  }

  .explanation {
    font-size: 0.8125rem;
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
    padding: 6rem 2rem;
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

  .tip-body h3 {
    font-size: 1.5rem;
  }

  .tip-body .margin {
    font-size: 0.875rem;
  }

  .explanation {
    font-size: 0.9375rem;
  }

  .heuristic-btn {
    padding: 0.75rem 1.5rem;
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

  .heuristic-badge {
    font-size: 0.75rem;
  }

  .confidence {
    font-size: 0.875rem;
  }

}
</style>
