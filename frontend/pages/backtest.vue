<template>
  <div>
    <Header />
    <main class="main">
      <section class="hero">
        <h1>Backtesting</h1>
        <p>See how our heuristics performed historically.</p>
      </section>

      <section class="section">
        <h2>Performance Comparison</h2>
        
        <div class="controls">
          <select v-model="selectedSeason" class="select" :disabled="seasonsLoading">
            <option v-if="seasonsLoading" disabled>Loading seasons...</option>
            <option v-for="year in availableYears" :key="year" :value="year">
              {{ year }}
            </option>
          </select>
          <button @click="runBacktest" class="btn btn-primary" :disabled="loading || seasonsLoading">Run Backtest</button>
        </div>

        <div v-if="loading" class="loading">
          <div class="spinner"></div>
        </div>
        <div v-else-if="error" class="error">
          <p>{{ error }}</p>
        </div>
        <div v-else-if="comparison" class="comparison">
          <div v-for="(stats, heuristic) in comparison.comparison" :key="heuristic" class="stat-card">
            <h3>{{ formatHeuristic(heuristic) }}</h3>
            <div class="stat-grid">
              <div class="stat">
                <span class="stat-label">Accuracy</span>
                <span class="stat-value">{{ (stats.overall_accuracy * 100).toFixed(1) }}%</span>
              </div>
              <div class="stat">
                <span class="stat-label">Profit</span>
                <span class="stat-value" :class="{ positive: stats.total_profit > 0, negative: stats.total_profit < 0 }">
                  ${{ stats.total_profit.toFixed(2) }}
                </span>
              </div>
              <div class="stat">
                <span class="stat-label">Tips</span>
                <span class="stat-value">{{ stats.total_tips }}</span>
              </div>
              <div class="stat">
                <span class="stat-label">Rounds</span>
                <span class="stat-value">{{ stats.total_rounds }}</span>
              </div>
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

const loading = ref(false)
const seasonsLoading = ref(true)
const error = ref<string | null>(null)
const comparison = ref<any>(null)
const selectedSeason = ref(new Date().getFullYear() - 1)
const availableYears = ref<number[]>([])

const generateFallbackYears = (currentYear: number): number[] => {
  const years: number[] = []
  for (let year = 2010; year < currentYear; year++) {
    years.push(year)
  }
  return years.sort((a, b) => b - a) // Descending order
}

const loadAvailableSeasons = async () => {
  seasonsLoading.value = true
  try {
    const response = await api.getAvailableSeasons()
    const currentYear = response.current_year
    
    // Filter out current year from available years
    const filteredYears = response.available_years.filter((year: number) => year !== currentYear)
    
    if (filteredYears.length > 0) {
      availableYears.value = filteredYears
    } else {
      // Fallback to generated years if no data exists
      availableYears.value = generateFallbackYears(currentYear)
    }
    
    // Set default selected season to the first available year (newest)
    if (availableYears.value.length > 0) {
      selectedSeason.value = availableYears.value[0]
    }
  } catch (e) {
    console.error('Failed to load available seasons:', e)
    // Fallback to generated years on error
    const currentYear = new Date().getFullYear()
    availableYears.value = generateFallbackYears(currentYear)
    if (availableYears.value.length > 0) {
      selectedSeason.value = availableYears.value[0]
    }
  } finally {
    seasonsLoading.value = false
  }
}

const runBacktest = async () => {
  loading.value = true
  error.value = null
  
  try {
    comparison.value = await api.compareHeuristics(selectedSeason.value)
  } catch (e) {
    error.value = 'Failed to run backtest'
    console.error(e)
  } finally {
    loading.value = false
  }
}

const formatHeuristic = (h: string) => {
  const labels: Record<string, string> = {
    best_bet: 'Best Bet',
    yolo: 'YOLO',
    high_risk_high_reward: 'High Risk / High Reward'
  }
  return labels[h] || h
}

onMounted(async () => {
  await loadAvailableSeasons()
  runBacktest()
})
</script>

<style scoped>
.main {
  max-width: 1400px;
  margin: 0 auto;
  padding: 2rem;
}

.hero {
  padding: 4rem 2rem;
  text-align: center;
}

.hero h1 {
  font-size: clamp(2.5rem, 8vw, 4rem);
  margin-bottom: 1rem;
}

.section {
  padding: 4rem 2rem;
}

.section h2 {
  text-align: center;
  margin-bottom: 2rem;
}

.controls {
  display: flex;
  gap: 1rem;
  justify-content: center;
  margin-bottom: 3rem;
}

.select {
  padding: 0.875rem 2rem;
  font-size: 1rem;
  font-weight: 700;
  border: 2px solid var(--color-text);
  background: var(--color-bg);
  color: var(--color-text);
  cursor: pointer;
}

.loading, .error {
  text-align: center;
  padding: 4rem 2rem;
}

.comparison {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 2rem;
}

.stat-card {
  border: 1px solid var(--color-border);
  padding: 2rem;
}

.stat-card h3 {
  font-size: 1.25rem;
  margin-bottom: 1.5rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.stat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.stat-label {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.stat-value {
  font-size: 1.5rem;
  font-weight: 800;
}

.stat-value.positive {
  color: #00a000;
}

.stat-value.negative {
  color: #c00000;
}
</style>
