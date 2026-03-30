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
          <span class="round-label">Current Round</span>
          <span class="round-value">R{{ latestRound.round_id }} • {{ latestRound.season }}</span>
          <span class="game-count">{{ latestRound.game_count }} Games</span>
        </div>

        <!-- Heuristic Selector -->
        <div class="heuristic-selector">
          <button
            v-for="heuristic in heuristics"
            :key="heuristic.value"
            @click="selectedHeuristic = heuristic.value"
            :class="['heuristic-btn', { active: selectedHeuristic === heuristic.value }]"
          >
            {{ heuristic.label }}
          </button>
        </div>

        <!-- Tips Display -->
        <div v-if="loading" class="loading">
          <div class="spinner"></div>
        </div>
        <div v-else-if="error" class="error">
          <p>{{ error }}</p>
          <button @click="loadTips" class="btn">Retry</button>
        </div>
        <div v-else-if="filteredTips.length === 0" class="empty">
          <p>No tips available for this round.</p>
          <button @click="generateTips" class="btn btn-primary">Generate Tips</button>
        </div>
        <div v-else class="tips-grid">
          <TipCard
            v-for="tip in filteredTips"
            :key="tip.id"
            :heuristic="tip.heuristic"
            :selected-team="tip.selected_team"
            :margin="tip.margin"
            :confidence="tip.confidence"
            :explanation="tip.explanation"
          />
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
const tips = ref<any[]>([])
const latestRound = ref<any>(null)
const selectedHeuristic = ref<string>('all')

const heuristics = [
  { value: 'all', label: 'All' },
  { value: 'best_bet', label: 'Best Bet' },
  { value: 'yolo', label: 'YOLO' },
  { value: 'high_risk_high_reward', label: 'High Risk' }
]

const filteredTips = computed(() => {
  if (selectedHeuristic.value === 'all') {
    return tips.value
  }
  return tips.value.filter(tip => tip.heuristic === selectedHeuristic.value)
})

const loadLatestRound = async () => {
  try {
    latestRound.value = await api.getLatestRound()
  } catch (e) {
    console.error('Failed to load latest round:', e)
  }
}

const loadTips = async () => {
  loading.value = true
  error.value = null
  
  try {
    const data = await api.getTips()
    tips.value = data.tips || []
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
    await api.generateTips(season, round)
    await loadTips()
  } catch (e) {
    error.value = 'Failed to generate tips'
    console.error(e)
  }
}

onMounted(() => {
  loadLatestRound()
  loadTips()
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

.tips-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
}
</style>
