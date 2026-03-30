<template>
  <div>
    <Header />
    <main class="main">
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
          <div v-for="game in gamesWithTips" :key="game.id" class="game-card">
            <!-- Match Info -->
            <div class="match-info">
              <div class="teams">
                <span class="team home">{{ game.home_team }}</span>
                <span class="vs">VS</span>
                <span class="team away">{{ game.away_team }}</span>
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
        </div>
      </section>
    </main>
    <Footer />
  </div>
</template>

<script setup lang="ts">
const api = useApi()

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
.main {
  max-width: 1400px;
  margin: 0 auto;
  padding: 2rem;
}

.hero {
  padding: 6rem 2rem;
  text-align: center;
}

.hero h1 {
  font-size: clamp(3rem, 10vw, 6rem);
  line-height: 0.95;
  margin-bottom: 1.5rem;
}

.hero p {
  font-size: 1.25rem;
  max-width: 600px;
  margin: 0 auto;
}

.section {
  padding: 4rem 2rem;
}

/* Round Display */
.round-display {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  padding: 1.5rem;
  border: 1px solid var(--color-border);
  margin-bottom: 2rem;
}

.round-label {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.round-value {
  font-size: 1.5rem;
  font-weight: 800;
}

.game-count {
  font-size: 0.875rem;
  color: var(--color-muted);
}

/* Data Warning */
.data-warning {
  padding: 1rem 1.5rem;
  background: rgba(255, 193, 7, 0.1);
  border: 1px solid rgba(255, 193, 7, 0.3);
  border-radius: 8px;
  margin-bottom: 2rem;
  text-align: center;
}

.data-warning p {
  margin: 0;
  font-size: 0.9rem;
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
  margin-bottom: 3rem;
  flex-wrap: wrap;
}

.heuristic-btn {
  padding: 0.75rem 1.5rem;
  font-size: 0.875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border: 2px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text);
  cursor: pointer;
  transition: all 0.2s ease;
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
  padding: 4rem 2rem;
}

/* Games Grid */
.games-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
  gap: 2rem;
}

.game-card {
  border: 1px solid var(--color-border);
  padding: 2rem;
  transition: border-color 0.2s ease;
}

.game-card:hover {
  border-color: var(--color-text);
}

/* Match Info */
.match-info {
  margin-bottom: 2rem;
  padding-bottom: 1.5rem;
  border-bottom: 1px solid var(--color-border);
}

.teams {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.team {
  flex: 1;
  font-size: 1.25rem;
  font-weight: 700;
}

.team.home {
  text-align: right;
}

.team.away {
  text-align: left;
}

.vs {
  font-size: 0.875rem;
  font-weight: 700;
  padding: 0 1rem;
}

.match-details {
  display: flex;
  justify-content: space-between;
  font-size: 0.875rem;
  color: var(--color-muted);
}

/* Tip Info */
.tip-info {
  background: var(--color-hover);
  padding: 1.5rem;
  border-radius: 4px;
}

.tip-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.heuristic-badge {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.confidence {
  font-size: 0.875rem;
  font-weight: 700;
}

.tip-body h3 {
  font-size: 1.5rem;
  margin-bottom: 0.5rem;
}

.tip-body .margin {
  font-size: 0.875rem;
  margin: 0;
}

.explanation {
  margin-top: 1rem;
  font-size: 0.9375rem;
  line-height: 1.5;
}

.no-tip {
  text-align: center;
  padding: 2rem;
  color: var(--color-muted);
}
</style>
