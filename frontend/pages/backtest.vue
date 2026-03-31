<template>
  <div>
    <Header />
    <main class="main">
      <section class="hero">
        <h1>Backtesting</h1>
        <p>See how our heuristics performed historically.</p>
      </section>

      <!-- Current Season Section -->
      <section v-if="currentSeasonData" class="current-season-section">
        <div class="current-season-header">
          <h2>
            <span class="badge">🏆 Current Season {{ currentSeasonData.season }}</span>
          </h2>
          <p class="season-progress">
            {{ currentSeasonData.rounds_completed }} / {{ currentSeasonData.total_rounds }} rounds completed
          </p>
        </div>
        
        <div v-if="currentSeasonLoading" class="loading">
          <div class="spinner"></div>
        </div>
        <div v-else-if="currentSeasonError" class="error">
          <p>{{ currentSeasonError }}</p>
        </div>
        <div v-else class="current-season-cards">
          <div v-for="heuristic in currentSeasonData.heuristics" :key="heuristic.heuristic" class="current-season-card">
            <div class="card-header">
              <h3>{{ formatHeuristic(heuristic.heuristic) }}</h3>
              <span class="heuristic-badge">Current Season</span>
            </div>
            <div class="card-stats">
              <div class="stat-row">
                <span class="stat-label">Year-to-Date Profit</span>
                <span class="stat-value" :class="{ positive: heuristic.total_profit > 0, negative: heuristic.total_profit < 0 }">
                  ${{ heuristic.total_profit.toFixed(2) }}
                </span>
              </div>
              <div class="stat-row">
                <span class="stat-label">Projected Annual Profit</span>
                <span class="stat-value projected" :class="{ positive: heuristic.projected_annual_profit > 0, negative: heuristic.projected_annual_profit < 0 }">
                  ${{ heuristic.projected_annual_profit.toFixed(2) }}
                </span>
              </div>
              <div class="stat-row">
                <span class="stat-label">Accuracy</span>
                <span class="stat-value">{{ (heuristic.total_accuracy * 100).toFixed(1) }}%</span>
              </div>
              <div class="stat-row">
                <span class="stat-label">Rounds Played</span>
                <span class="stat-value">{{ heuristic.rounds_played }}</span>
              </div>
              <div class="stat-row">
                <span class="stat-label">Avg Profit/Round</span>
                <span class="stat-value" :class="{ positive: heuristic.avg_profit_per_round > 0, negative: heuristic.avg_profit_per_round < 0 }">
                  ${{ heuristic.avg_profit_per_round.toFixed(2) }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="section">
        <h2>Performance Comparison</h2>
        
        <div class="controls">
          <select v-model="selectedSeason" class="select" :disabled="seasonsLoading || syncing">
            <option v-if="seasonsLoading" disabled>Loading seasons...</option>
            <option v-for="year in availableYears" :key="year" :value="year">
              {{ year }}
            </option>
          </select>
          <div class="view-toggle">
            <button
              @click="viewMode = 'summary'"
              class="toggle-btn"
              :class="{ active: viewMode === 'summary' }"
            >
              Summary
            </button>
            <button
              @click="viewMode = 'table'"
              class="toggle-btn"
              :class="{ active: viewMode === 'table' }"
            >
              Detailed Table
            </button>
            <button
              @click="viewMode = 'charts'"
              class="toggle-btn"
              :class="{ active: viewMode === 'charts' }"
            >
              Charts
            </button>
          </div>
        </div>

        <div v-if="syncing" class="loading sync-message">
          <div class="spinner"></div>
          <p>Syncing historical data for {{ selectedSeason }}...</p>
        </div>
        <div v-else-if="loading" class="loading">
          <div class="spinner"></div>
        </div>
        <div v-else-if="error" class="error">
          <p>{{ error }}</p>
        </div>
        
        <!-- Summary View -->
        <div v-else-if="viewMode === 'summary' && comparison" class="comparison">
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
        
        <!-- Table View -->
        <div v-else-if="viewMode === 'table'" class="table-section">
          <div v-if="syncing" class="loading sync-message">
            <div class="spinner"></div>
            <p>Syncing historical data for {{ selectedSeason }}...</p>
          </div>
          <div v-else-if="tableLoading" class="loading">
            <div class="spinner"></div>
          </div>
          <div v-else-if="tableError" class="error">
            <p>{{ tableError }}</p>
          </div>
          <div v-else-if="tableData && tableData.heuristics.length > 0" class="tables-container">
            <div v-for="heuristicData in tableData.heuristics" :key="heuristicData.heuristic" class="table-wrapper">
              <div class="table-header">
                <h3>{{ formatHeuristic(heuristicData.heuristic) }}</h3>
                <div class="table-summary">
                  <span class="summary-item">
                    <strong>Total Accuracy:</strong> {{ (heuristicData.total_accuracy * 100).toFixed(1) }}%
                  </span>
                  <span class="summary-item">
                    <strong>Total Profit:</strong>
                    <span :class="{ positive: heuristicData.total_profit > 0, negative: heuristicData.total_profit < 0 }">
                      ${{ heuristicData.total_profit.toFixed(2) }}
                    </span>
                  </span>
                </div>
              </div>
              <div class="table-scroll">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>Round</th>
                      <th>Tips Made</th>
                      <th>Tips Correct</th>
                      <th>Accuracy</th>
                      <th>Profit</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="round in heuristicData.rounds" :key="round.round_id">
                      <td>{{ round.round_id }}</td>
                      <td>{{ round.tips_made }}</td>
                      <td>{{ round.tips_correct }}</td>
                      <td>{{ (round.accuracy * 100).toFixed(1) }}%</td>
                      <td :class="{ positive: round.profit > 0, negative: round.profit < 0 }">
                        ${{ round.profit.toFixed(2) }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          <div v-else class="empty-state">
            <p>No table data available for this season.</p>
          </div>
        </div>
        
        <!-- Charts View -->
        <div v-else-if="viewMode === 'charts'" class="charts-section">
          <div v-if="syncing" class="loading sync-message">
            <div class="spinner"></div>
            <p>Syncing historical data for {{ selectedSeason }}...</p>
          </div>
          <div v-else-if="chartsLoading" class="loading">
            <div class="spinner"></div>
          </div>
          <div v-else-if="chartsError" class="error">
            <p>{{ chartsError }}</p>
          </div>
          <div v-else-if="chartData && chartData.length > 0" class="charts-container">
            <div class="charts-grid">
              <ProfitChart :data="chartData" :loading="chartsLoading" />
              <AccuracyChart :data="chartData" :loading="chartsLoading" />
            </div>
            <div class="charts-full-width">
              <CumulativeProfitChart :data="chartData" :loading="chartsLoading" />
            </div>
          </div>
          <div v-else class="empty-state">
            <p>No chart data available for this season.</p>
          </div>
        </div>
      </section>
    </main>
    <Footer />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'

const api = useApi()

// Page-specific SEO
useHead({
  title: 'Backtesting | AFL Prediction Performance & Accuracy',
  meta: [
    { name: 'description', content: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit, accuracy rates, and betting performance across multiple seasons.' },
    { name: 'keywords', content: 'AFL backtesting, AFL prediction accuracy, AFL betting performance, AFL tipping results, AFL profit analysis, AFL historical performance' },
    { property: 'og:title', content: 'Backtesting | AFL Prediction Performance & Accuracy' },
    { property: 'og:description', content: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit and accuracy rates.' },
    { property: 'og:url', content: 'https://whatismytip.com/backtest' },
    { name: 'twitter:title', content: 'Backtesting | AFL Prediction Performance & Accuracy' },
    { name: 'twitter:description', content: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit and accuracy rates.' }
  ],
  script: [
    {
      type: 'application/ld+json',
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'WebPage',
        name: 'AFL Prediction Backtesting',
        description: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit, accuracy rates, and betting performance.',
        url: 'https://whatismytip.com/backtest',
        mainEntity: {
          '@type': 'Dataset',
          name: 'AFL Prediction Performance Data',
          description: 'Historical performance data for AFL prediction heuristics including accuracy, profit, and betting results'
        }
      })
    }
  ]
})

const loading = ref(false)
const seasonsLoading = ref(true)
const tableLoading = ref(false)
const chartsLoading = ref(false)
const syncing = ref(false)
const currentSeasonLoading = ref(false)
const error = ref<string | null>(null)
const tableError = ref<string | null>(null)
const chartsError = ref<string | null>(null)
const currentSeasonError = ref<string | null>(null)
const comparison = ref<any>(null)
const tableData = ref<any>(null)
const chartData = ref<any>(null)
const currentSeasonData = ref<any>(null)
const viewMode = ref<'summary' | 'table' | 'charts'>('summary')
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

const loadComparisonData = async () => {
  loading.value = true
  syncing.value = true
  error.value = null
  
  try {
    comparison.value = await api.compareHeuristics(selectedSeason.value)
  } catch (e) {
    error.value = 'Failed to load comparison data'
    console.error(e)
  } finally {
    loading.value = false
    syncing.value = false
  }
}

const loadTableData = async () => {
  tableLoading.value = true
  syncing.value = true
  tableError.value = null
  
  try {
    tableData.value = await api.getBacktestTableData(selectedSeason.value)
  } catch (e) {
    tableError.value = 'Failed to load table data'
    console.error(e)
  } finally {
    tableLoading.value = false
    syncing.value = false
  }
}

// Watch for season changes to reload data
watch(selectedSeason, async () => {
  if (viewMode.value === 'summary') {
    await loadComparisonData()
  } else if (viewMode.value === 'table') {
    await loadTableData()
  }
})

// Watch for view mode changes
watch(viewMode, async (newMode) => {
  if (newMode === 'summary' && !comparison.value) {
    await loadComparisonData()
  } else if (newMode === 'table' && !tableData.value) {
    await loadTableData()
  } else if (newMode === 'charts' && !chartData.value) {
    await loadChartData()
  }
})

const loadChartData = async () => {
  chartsLoading.value = true
  syncing.value = true
  chartsError.value = null
  
  try {
    const tableResponse = await api.getBacktestTableData(selectedSeason.value)
    // Transform table data into chart-friendly format
    chartData.value = tableResponse.heuristics.map((h: any) => ({
      heuristic: h.heuristic,
      rounds: h.rounds.map((r: any) => ({
        round_id: r.round_id,
        profit: r.profit,
        accuracy: r.accuracy
      }))
    }))
  } catch (e) {
    chartsError.value = 'Failed to load chart data'
    console.error(e)
  } finally {
    chartsLoading.value = false
    syncing.value = false
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

const loadCurrentSeasonData = async () => {
  currentSeasonLoading.value = true
  currentSeasonError.value = null
  
  try {
    currentSeasonData.value = await api.getCurrentSeasonPerformance()
  } catch (e) {
    currentSeasonError.value = 'Failed to load current season data'
    console.error(e)
  } finally {
    currentSeasonLoading.value = false
  }
}

onMounted(async () => {
  await loadAvailableSeasons()
  await loadCurrentSeasonData()
  await loadComparisonData()
})
</script>

<style scoped>
.main {
  max-width: 1400px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}

.hero {
  padding: 3rem 1.5rem;
  text-align: center;
}

.hero h1 {
  font-size: clamp(2rem, 6vw, 4rem);
  margin-bottom: 1rem;
}

/* Current Season Styles */
.current-season-section {
  padding: 2rem 1.5rem;
  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
  border-bottom: 2px solid var(--color-border);
}

.current-season-header {
  text-align: center;
  margin-bottom: 1.5rem;
}

.current-season-header h2 {
  margin-bottom: 0.5rem;
}

.badge {
  display: inline-block;
  padding: 0.375rem 0.75rem;
  background: var(--color-text);
  color: var(--color-bg);
  border-radius: 2rem;
  font-weight: 700;
  font-size: 0.8125rem;
}

.season-progress {
  font-size: 0.9375rem;
  color: var(--color-muted);
  font-weight: 600;
}

.current-season-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.25rem;
}

.current-season-card {
  background: var(--color-bg);
  border: 2px solid var(--color-text);
  border-radius: 0.75rem;
  padding: 1.25rem;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.25rem;
  padding-bottom: 0.875rem;
  border-bottom: 1px solid var(--color-border);
}

.card-header h3 {
  font-size: 1rem;
  font-weight: 700;
  margin: 0;
}

.heuristic-badge {
  padding: 0.25rem 0.625rem;
  background: #10b981;
  color: white;
  border-radius: 1rem;
  font-size: 0.6875rem;
  font-weight: 600;
}

.card-stats {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.stat-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.stat-label {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-muted);
}

.stat-value {
  font-size: 1rem;
  font-weight: 700;
}

.stat-value.positive {
  color: #00a000;
}

.stat-value.negative {
  color: #c00000;
}

.stat-value.projected {
  font-size: 1.125rem;
  font-weight: 800;
}

.section {
  padding: 3rem 1.5rem;
}

.section h2 {
  text-align: center;
  margin-bottom: 1.5rem;
}

.controls {
  display: flex;
  gap: 0.75rem;
  justify-content: center;
  align-items: center;
  margin-bottom: 2rem;
  flex-wrap: wrap;
}

.view-toggle {
  display: flex;
  gap: 0.375rem;
  background: var(--color-bg);
  border: 2px solid var(--color-text);
  border-radius: 0.5rem;
  padding: 0.25rem;
}

.toggle-btn {
  padding: 0.5rem 0.75rem;
  background: transparent;
  border: none;
  color: var(--color-text);
  cursor: pointer;
  font-weight: 600;
  font-size: 0.8125rem;
  border-radius: 0.25rem;
  transition: all 0.2s ease;
  min-height: 44px;
  min-width: 44px;
}

.toggle-btn:hover {
  background: var(--color-border);
}

.toggle-btn.active {
  background: var(--color-text);
  color: var(--color-bg);
}

.select {
  padding: 0.75rem 1.5rem;
  font-size: 0.9375rem;
  font-weight: 700;
  border: 2px solid var(--color-text);
  background: var(--color-bg);
  color: var(--color-text);
  cursor: pointer;
  min-height: 44px;
}

.select:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.loading, .error {
  text-align: center;
  padding: 3rem 1.5rem;
}

.sync-message {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
}

.sync-message p {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text);
}

.comparison {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1.5rem;
}

.stat-card {
  border: 1px solid var(--color-border);
  padding: 1.5rem;
}

.stat-card h3 {
  font-size: 1.125rem;
  margin-bottom: 1.25rem;
  padding-bottom: 0.875rem;
  border-bottom: 1px solid var(--color-border);
}

.stat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.25rem;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.stat-label {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.stat-value {
  font-size: 1.25rem;
  font-weight: 800;
}

.stat-value.positive {
  color: #00a000;
}

.stat-value.negative {
  color: #c00000;
}

/* Table Styles */
.table-section {
  padding: 1rem 0;
}

.tables-container {
  display: flex;
  flex-direction: column;
  gap: 2rem;
}

.table-wrapper {
  border: 1px solid var(--color-border);
  padding: 1.5rem;
}

.table-header {
  margin-bottom: 1.25rem;
}

.table-header h3 {
  font-size: 1.125rem;
  margin-bottom: 0.875rem;
}

.table-summary {
  display: flex;
  gap: 1.5rem;
  font-size: 0.8125rem;
  flex-wrap: wrap;
}

.summary-item {
  color: var(--color-muted);
}

.table-scroll {
  overflow-x: auto;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.data-table thead {
  background: var(--color-border);
}

.data-table th {
  padding: 0.625rem 0.875rem;
  text-align: left;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 0.6875rem;
}

.data-table tbody tr {
  border-bottom: 1px solid var(--color-border);
}

.data-table tbody tr:last-child {
  border-bottom: none;
}

.data-table td {
  padding: 0.625rem 0.875rem;
}

.data-table td.positive {
  color: #00a000;
  font-weight: 600;
}

.data-table td.negative {
  color: #c00000;
  font-weight: 600;
}

.empty-state {
  text-align: center;
  padding: 3rem 1.5rem;
  color: var(--color-muted);
}

/* Charts Styles */
.charts-section {
  padding: 1rem 0;
}

.charts-container {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.charts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
  gap: 1.5rem;
}

.charts-full-width {
  width: 100%;
}

/* Mobile styles */
@media (max-width: 640px) {
  .main {
    padding: 1.5rem 1rem;
  }

  .hero {
    padding: 2.5rem 1rem;
  }

  .hero h1 {
    margin-bottom: 0.875rem;
  }

  .current-season-section {
    padding: 1.5rem 1rem;
  }

  .current-season-header {
    margin-bottom: 1rem;
  }

  .badge {
    font-size: 0.75rem;
    padding: 0.25rem 0.625rem;
  }

  .season-progress {
    font-size: 0.875rem;
  }

  .current-season-cards {
    grid-template-columns: 1fr;
    gap: 1rem;
  }

  .current-season-card {
    padding: 1rem;
  }

  .card-header h3 {
    font-size: 0.9375rem;
  }

  .stat-row {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.25rem;
  }

  .stat-value {
    font-size: 1.125rem;
  }

  .stat-value.projected {
    font-size: 1.25rem;
  }

  .section {
    padding: 2rem 1rem;
  }

  .section h2 {
    margin-bottom: 1.25rem;
  }

  .controls {
    flex-direction: column;
    gap: 1rem;
    width: 100%;
  }

  .view-toggle {
    width: 100%;
  }

  .toggle-btn {
    flex: 1;
    padding: 0.5rem 0.625rem;
    font-size: 0.75rem;
  }

  .select {
    width: 100%;
    padding: 0.625rem 1rem;
  }

  .comparison {
    grid-template-columns: 1fr;
    gap: 1rem;
  }

  .stat-card {
    padding: 1.25rem;
  }

  .stat-card h3 {
    font-size: 1rem;
    margin-bottom: 1rem;
  }

  .stat-grid {
    grid-template-columns: 1fr;
    gap: 1rem;
  }

  .stat-value {
    font-size: 1.125rem;
  }

  .tables-container {
    gap: 1.5rem;
  }

  .table-wrapper {
    padding: 1rem;
  }

  .table-header h3 {
    font-size: 1rem;
  }

  .table-summary {
    flex-direction: column;
    gap: 0.5rem;
  }

  .data-table {
    font-size: 0.75rem;
  }

  .data-table th,
  .data-table td {
    padding: 0.5rem 0.625rem;
  }

  .charts-grid {
    grid-template-columns: 1fr;
    gap: 1rem;
  }

  .loading, .error {
    padding: 2rem 1rem;
  }

  .sync-message p {
    font-size: 0.9375rem;
  }
}

/* Tablet styles */
@media (min-width: 641px) and (max-width: 1024px) {
  .main {
    padding: 2rem 1.5rem;
  }

  .hero {
    padding: 3.5rem 1.5rem;
  }

  .current-season-section {
    padding: 2.5rem 1.5rem;
  }

  .current-season-cards {
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  }

  .charts-grid {
    grid-template-columns: 1fr;
  }
}

/* Desktop styles */
@media (min-width: 1025px) {
  .main {
    padding: 2rem;
  }

  .hero {
    padding: 4rem 2rem;
  }

  .hero h1 {
    font-size: clamp(2.5rem, 8vw, 4rem);
  }

  .current-season-section {
    padding: 3rem 2rem;
  }

  .current-season-header {
    margin-bottom: 2rem;
  }

  .badge {
    padding: 0.5rem 1rem;
    font-size: 0.875rem;
  }

  .season-progress {
    font-size: 1rem;
  }

  .current-season-cards {
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 1.5rem;
  }

  .current-season-card {
    padding: 1.5rem;
  }

  .card-header h3 {
    font-size: 1.125rem;
  }

  .card-header {
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
  }

  .card-stats {
    gap: 1rem;
  }

  .stat-label {
    font-size: 0.875rem;
  }

  .stat-value {
    font-size: 1.125rem;
  }

  .stat-value.projected {
    font-size: 1.25rem;
  }

  .section {
    padding: 4rem 2rem;
  }

  .section h2 {
    margin-bottom: 2rem;
  }

  .controls {
    gap: 1rem;
    margin-bottom: 3rem;
  }

  .toggle-btn {
    padding: 0.5rem 1rem;
    font-size: 0.875rem;
  }

  .select {
    padding: 0.875rem 2rem;
    font-size: 1rem;
  }

  .loading, .error {
    padding: 4rem 2rem;
  }

  .sync-message p {
    font-size: 1.125rem;
  }

  .comparison {
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 2rem;
  }

  .stat-card {
    padding: 2rem;
  }

  .stat-card h3 {
    font-size: 1.25rem;
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
  }

  .stat-grid {
    grid-template-columns: 1fr 1fr;
    gap: 1.5rem;
  }

  .stat-label {
    font-size: 0.75rem;
  }

  .stat-value {
    font-size: 1.5rem;
  }

  .tables-container {
    gap: 3rem;
  }

  .table-wrapper {
    padding: 2rem;
  }

  .table-header {
    margin-bottom: 1.5rem;
  }

  .table-header h3 {
    font-size: 1.25rem;
    margin-bottom: 1rem;
  }

  .table-summary {
    gap: 2rem;
    font-size: 0.875rem;
  }

  .data-table {
    font-size: 0.875rem;
  }

  .data-table th {
    padding: 0.75rem 1rem;
    font-size: 0.75rem;
  }

  .data-table td {
    padding: 0.75rem 1rem;
  }

  .empty-state {
    padding: 4rem 2rem;
  }

  .charts-container {
    gap: 2rem;
  }

  .charts-grid {
    grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
    gap: 2rem;
  }
}
</style>
