<template>
  <section class="hero">
        <h1>AI-Powered<br>Footy Tipping</h1>
        <p>Smart heuristics. Clear explanations. Better tips.</p>
      </section>

      <section class="section">
        <!-- Round Display -->
        <div v-if="latestRound" class="round-display">
          <span class="round-label">{{ latestRound.is_current_year ? 'Current Round' : 'Latest Available' }}</span>
          <span class="round-value">R{{ latestRound.round_id }} • {{ latestRound.season }}</span>
          <span class="game-count">{{ latestRound.game_count }} Games</span>
        </div>
        
        <!-- Data Warning -->
        <div v-if="latestRound && !latestRound.is_current_year" class="data-warning">
          <p>
            <strong>No data available for {{ new Date().getFullYear() }}.</strong>
            Showing historical data from {{ latestRound.season }}.
          </p>
        </div>

        <!-- Heuristic Selector -->
        <div class="heuristic-selector">
          <button
            v-for="h in heuristics"
            :key="h.value"
            @click="selectedHeuristic = h.value"
            :class="['heuristic-btn', { active: selectedHeuristic === h.value }]"
          >
            {{ h.label }}
          </button>
        </div>

        <!-- Games with Tips -->
        <div v-if="loading" class="loading">
          <div class="spinner"></div>
        </div>
        <div v-else-if="error" class="error">
          <p>{{ error }}</p>
          <button @click="loadGames" class="btn">Retry</button>
        </div>
        <div v-else-if="gamesWithTips.length === 0" class="empty">
          <p>No tips available for this round.</p>
          <button @click="generateTips" class="btn btn-primary">Generate Tips</button>
        </div>
        <div v-else class="games-grid">
          <NuxtLink
            v-for="game in gamesWithTips"
            :key="game.id"
            :to="`/game/${game.id}`"
            class="game-card-link"
          >
            <div class="game-card">
              <!-- Match Info -->
            <div class="match-info">
              <div class="teams">
                <div class="team home">
                  <img :src="getLogoUrl(game.home_team)" :alt="game.home_team" class="team-logo" />
                </div>
                <span class="vs">VS</span>
                <div class="team away">
                  <img :src="getLogoUrl(game.away_team)" :alt="game.away_team" class="team-logo" />
                </div>
              </div>
              <div class="match-details">
                <span class="venue">{{ game.venue }}</span>
                <span class="date">{{ formatDate(game.date) }}</span>
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
  </section>
</template>

<script setup lang="ts">
definePageMeta({
  layout: 'default'
})

const api = useApi()

// Page-specific SEO
useHead({
  title: 'AFL Tips & Predictions | AI-Powered Footy Tipping',
  meta: [
    { name: 'description', content: 'Get AI-powered AFL tips and predictions for the current round. Expert footy tipping advice with smart heuristics, betting tips, and round predictions backed by machine learning models.' },
    { name: 'keywords', content: 'AFL tips, AFL predictions, AFL betting tips, AFL footy tips, AFL round predictions, AFL betting advice, footy tipping, AFL betting' },
    { property: 'og:title', content: 'AFL Tips & Predictions | AI-Powered Footy Tipping' },
    { property: 'og:description', content: 'Get AI-powered AFL tips and predictions for the current round. Expert footy tipping advice with smart heuristics.' },
    { property: 'og:url', content: 'https://whatismytip.com' },
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
        url: 'https://whatismytip.com',
        mainEntity: {
          '@type': 'SportsEvent',
          sport: 'Australian Rules Football',
          name: 'AFL Tips'
        }
      })
    }
  ]
})

const loading = ref(true)
const error = ref<string | null>(null)
const gamesWithTips = ref<any[]>([])
const latestRound = ref<any>(null)
const selectedHeuristic = ref<string>('best_bet')

const heuristics = [
  { value: 'best_bet', label: 'Best Bet' },
  { value: 'yolo', label: 'YOLO' },
  { value: 'high_risk_high_reward', label: 'High Risk' }
]

const loadLatestRound = async () => {
  try {
    latestRound.value = await api.getLatestRound()
  } catch (e) {
    console.error('Failed to load latest round:', e)
  }
}

const loadGames = async () => {
  loading.value = true
  error.value = null
  
  try {
    if (!latestRound.value) {
      await loadLatestRound()
    }
    
    const season = latestRound.value?.season || new Date().getFullYear()
    const round = latestRound.value?.round_id || 1
    
    const data = await api.getGamesWithTips(season, round, selectedHeuristic.value)
    gamesWithTips.value = data.games || []
  } catch (e) {
    error.value = 'Failed to load tips'
    console.error(e)
  } finally {
    loading.value = false
  }
}

const generateTips = async () => {
  try {
    const season = latestRound.value?.season || new Date().getFullYear()
    const round = latestRound.value?.round_id || 1
    await api.generateTips(season, round, [selectedHeuristic.value])
    await loadGames()
  } catch (e) {
    error.value = 'Failed to generate tips'
    console.error(e)
  }
}

const formatDate = (dateStr: string) => {
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-AU', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit'
  })
}

const formatHeuristic = (h: string) => {
  const labels: Record<string, string> = {
    best_bet: 'Best Bet',
    yolo: 'YOLO',
    high_risk_high_reward: 'High Risk'
  }
  return labels[h] || h
}

const getModelDisplayName = (modelName: string) => {
  const names: Record<string, string> = {
    elo: 'Elo Rating',
    form: 'Form',
    home_advantage: 'Home Advantage',
    value: 'Value'
  }
  return names[modelName] || modelName
}

const getLogoUrl = (teamName: string): string => {
  // Map team names to logo filenames
  const logoMap: Record<string, string> = {
    'Adelaide': 'Adelaide.png',
    'Brisbane Lions': 'Brisbane.png',
    'Carlton': 'Carlton.png',
    'Collingwood': 'Collingwood.png',
    'Essendon': 'Essendon.png',
    'Fremantle': 'Fremantle.png',
    'Geelong': 'Geelong.png',
    'Gold Coast': 'GoldCoast.png',
    'Greater Western Sydney': 'Giants.png',
    'Hawthorn': 'Hawthorn.png',
    'Melbourne': 'Melbourne.png',
    'North Melbourne': 'NorthMelbourne.png',
    'Port Adelaide': 'PortAdelaide.png',
    'Richmond': 'Richmond.png',
    'St Kilda': 'StKilda.png',
    'Sydney': 'Sydney.png',
    'West Coast': 'WestCoast.png',
    'Western Bulldogs': 'Bulldogs.png',
  }
  
  const filename = logoMap[teamName] || ''
  return filename ? `/logos/${filename}` : ''
}

// Reload games when heuristic changes
watch(selectedHeuristic, () => {
  loadGames()
})

onMounted(() => {
  loadLatestRound()
  loadGames()
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

/* Model Predictions */
.model-predictions {
  margin-top: 1.25rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--color-border);
}

.models-header {
  margin-bottom: 0.75rem;
}

.models-label {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.models-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.model-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.5rem 0;
  font-size: 0.8125rem;
}

.model-name {
  font-weight: 600;
  color: var(--color-muted);
}

.model-prediction {
  font-weight: 700;
}

.model-confidence {
  font-weight: 700;
  color: var(--color-text);
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
