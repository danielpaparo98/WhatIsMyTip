<template>
  <div class="game-detail-page">
    <!-- Loading State -->
    <div v-if="loading" class="loading" role="status" aria-live="polite">
      <div class="spinner"></div>
      <p>Loading game details...</p>
    </div>

    <!-- Error State -->
    <div v-else-if="error" class="error" role="status" aria-live="polite">
      <h2>Error</h2>
      <p>{{ error }}</p>    </div>

    <!-- Game Detail Content -->
    <div v-else-if="gameDetail" class="content">
      <NuxtLink to="/" class="back-link">← Back to Tips</NuxtLink>

      <!-- Header Section -->
      <section class="game-header-section">
        <div class="game-info">
          <div class="round-season">
            <span class="round">Round {{ gameDetail.game.round_id }}</span>
            <span class="season">{{ gameDetail.game.season }}</span>
            <span class="status" :class="{ completed: gameDetail.game.completed }">
              {{ gameDetail.game.completed ? 'Completed' : 'Upcoming' }}
            </span>
          </div>

          <div class="teams">
            <div class="team home">
              <img :src="getLogoUrl(gameDetail.game.home_team)" :alt="gameDetail.game.home_team + ' logo'" class="team-logo" loading="lazy" width="80" height="80" />
              <span class="team-name">{{ gameDetail.game.home_team }}</span>
              <span v-if="gameDetail.game.home_score !== null" class="score">{{ gameDetail.game.home_score }}</span>
            </div>
            
            <div class="vs">VS</div>
            
            <div class="team away">
              <img :src="getLogoUrl(gameDetail.game.away_team)" :alt="gameDetail.game.away_team + ' logo'" class="team-logo" loading="lazy" width="80" height="80" />
              <span class="team-name">{{ gameDetail.game.away_team }}</span>
              <span v-if="gameDetail.game.away_score !== null" class="score">{{ gameDetail.game.away_score }}</span>
            </div>
          </div>

          <div class="game-meta">
            <div class="meta-item">
              <span class="label">Venue:</span>
              <span class="value">{{ gameDetail.game.venue }}</span>
            </div>
            <div class="meta-item">
              <span class="label">Date:</span>
              <span class="value">{{ formatDate(gameDetail.game.date) }}</span>
            </div>
            <div class="meta-item">
              <span class="label">Time:</span>
              <span class="value">{{ formatTime(gameDetail.game.date) }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- Heuristic Tips Section -->
      <section class="tips-section">
        <h2 class="section-title">Heuristic Tips</h2>
        <div class="tips-grid">
          <TipCard
            v-for="tip in gameDetail.tips"
            :key="tip.id"
            :heuristic="tip.heuristic"
            :selected-team="tip.selected_team"
            :margin="tip.margin"
            :confidence="tip.confidence"
            :explanation="tip.explanation"
            :class="getHeuristicClass(tip.heuristic)"
          />
        </div>
      </section>

      <!-- Model Predictions Section -->
      <section class="models-section">
        <h2 class="section-title">Model Predictions</h2>
        <div class="models-grid">
          <div
            v-for="prediction in gameDetail.model_predictions"
            :key="prediction.model_name"
            class="model-card"
          >
            <div class="model-header">
              <span class="model-name">{{ getModelDisplayName(prediction.model_name) }}</span>
              <span class="confidence">{{ Math.round(prediction.confidence * 100) }}%</span>
            </div>
            <div class="model-body">
              <h3>{{ prediction.winner }}</h3>
              <p class="margin">Margin: {{ prediction.margin }} pts</p>
            </div>
          </div>
        </div>
      </section>

      <!-- Match Analysis / BBQ Talking Points -->
      <section v-if="gameDetail.match_analysis" class="match-analysis-section">
        <h2 class="section-title">Match Analysis</h2>
        <MatchAnalysisCard :analysis="gameDetail.match_analysis" />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { GameDetailResponse } from '~/composables/useApi'

const route = useRoute()
const { getGameDetail } = useApi()
const { getLogoUrl } = useTeamLogos()
const { formatDateShort, formatTime, getModelDisplayName } = useFormatters()

const gameDetail = ref<GameDetailResponse | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

// Fetch game detail on mount
onMounted(async () => {
  try {
    const slug = route.params.slug as string
    if (!slug || !/^[a-zA-Z0-9]{10,12}$/.test(slug)) {
      throw new Error('Invalid game slug')
    }
    
    gameDetail.value = await getGameDetail(slug)
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load game details'
  } finally {
    loading.value = false
  }
})

// Use shared formatters
const formatDate = formatDateShort

// Get heuristic display name
const getHeuristicClass = (heuristic: string): string => {
  const classes: Record<string, string> = {
    'best_bet': 'best-bet',
    'yolo': 'yolo',
    'high_risk_high_reward': 'high-risk'
  }
  return classes[heuristic] || ''
}

// Set page meta
useHead({
  title: () => gameDetail.value
    ? `${gameDetail.value.game.home_team} vs ${gameDetail.value.game.away_team}`
    : 'Game Details'
})
</script>

<style scoped>
.game-detail-page {
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
}

.back-link:hover {
  color: var(--color-text);
}

/* Content */
.content {
  max-width: 1200px;
  margin: 0 auto;
}

/* Game Header Section */
.game-header-section {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  padding: 2rem;
  margin-bottom: 2rem;
}

.game-header-section .back-link {
  margin-bottom: 1.5rem;
}

.game-info {
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
  color: var(--color-background);
}

.status.completed {
  background: #10b981;
  color: white;
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

/* Game Meta */
.game-meta {
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

/* Sections */
.section-title {
  font-size: 1.5rem;
  font-weight: 700;
  margin-bottom: 1.5rem;
}

.tips-section,
.models-section,
.match-analysis-section {
  margin-bottom: 2rem;
}

/* Tips Grid */
.tips-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1rem;
}

/* Heuristic-specific styling */
.tip-card.best-bet {
  border-left: 4px solid #10b981;
}

.tip-card.yolo {
  border-left: 4px solid #f97316;
}

.tip-card.high-risk {
  border-left: 4px solid #8b5cf6;
}

/* Models Grid */
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

.model-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--color-border);
}

.model-name {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.model-header .confidence {
  font-size: 0.8125rem;
  font-weight: 700;
}

.model-body h3 {
  font-size: 1.25rem;
  margin-bottom: 0.5rem;
}

.model-body .margin {
  font-size: 0.8125rem;
  margin: 0;
  color: var(--color-muted);
}

/* Mobile Responsive */
@media (max-width: 640px) {
  .game-detail-page {
    padding: 1rem 0.75rem;
  }

  .game-header-section {
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

  .tips-grid,
  .models-grid {
    grid-template-columns: 1fr;
  }

  .section-title {
    font-size: 1.25rem;
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
  .game-header-section {
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

  .tips-grid {
    grid-template-columns: repeat(3, 1fr);
  }

  .models-grid {
    grid-template-columns: repeat(4, 1fr);
  }
}
</style>
