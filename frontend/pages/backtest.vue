<template>
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
        
        <div v-if="currentSeasonLoading" class="loading" role="status" aria-live="polite">
          <div class="spinner"></div>
        </div>
        <div v-else-if="currentSeasonError" class="error" role="status" aria-live="polite">
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
              <div class="disclaimer">
                <small>⚠️ Projections are based on early season performance and may change as the season progresses.</small>
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

      <!-- Active Weighted Model Section -->
      <section class="active-model-section">
        <div class="active-model-header">
          <h2>Weighted Tip Model</h2>
          <span v-if="activeModelData?.active" class="version-badge">
            v{{ activeModelData.model?.version }}
          </span>
        </div>

        <div v-if="activeModelLoading" class="loading" role="status" aria-live="polite">
          <div class="spinner"></div>
        </div>
        <div v-else-if="activeModelError" class="error" role="status" aria-live="polite">
          <p>{{ activeModelError }}</p>
        </div>
        <div v-else-if="!activeModelData?.active" class="model-empty">
          <p>⏳ No trained Weighted Tip model yet. The model will be trained after the first weekly retrain job runs.</p>
          <p class="model-empty-sub">Until then, the Weighted Tip heuristic uses a majority-vote fallback.</p>
        </div>
        <div v-else-if="activeModelData.model" class="model-content">
          <div class="model-meta-row">
            <span class="meta-item">
              <strong>Trained:</strong> {{ formatDate(activeModelData.model.trained_at) }}
            </span>
            <span class="meta-item">
              <strong>Training rows:</strong> {{ activeModelData.model.training_rows }}
            </span>
            <span class="meta-item">
              <strong>Intercept:</strong> {{ activeModelData.model.intercept.toFixed(2) }}
            </span>
            <span v-if="activeModelData.model.metrics.r2 != null" class="meta-item">
              <strong>R²:</strong> {{ activeModelData.model.metrics.r2.toFixed(4) }}
            </span>
            <span v-if="activeModelData.model.metrics.mae != null" class="meta-item">
              <strong>MAE:</strong> {{ activeModelData.model.metrics.mae.toFixed(1) }}
            </span>
          </div>

          <div class="model-explanation">
            <p>{{ modelExplanationText }}</p>
          </div>

          <div v-if="groupedModelCoefficients.length > 0" class="coefficients-visual">
            <!-- Model Equation -->
            <div class="equation-card">
              <div class="equation-title">Model Equation</div>
              <div class="equation-display">
                <span class="eq-left">predicted_margin =</span>
                <span class="eq-intercept">{{ activeModelData.model!.intercept.toFixed(2) }}</span>
                <span v-for="(row, i) in groupedModelCoefficients" :key="row.model" class="eq-term">
                  <span class="eq-op">{{ row.margin_coef >= 0 ? '+' : '−' }}</span>
                  <span class="eq-coeff">{{ Math.abs(row.margin_coef).toFixed(3) }}</span>
                  <span class="eq-dot">·</span>
                  <span class="eq-model">{{ getModelDisplayName(row.model) }}<sub class="eq-sub">m</sub></span>
                </span>
              </div>
              <p class="equation-note">
                Each model contributes a margin weight (× its predicted margin toward home) and a confidence weight.
                Models with larger absolute weights have more influence on the final tip.
              </p>
            </div>

            <!-- Coefficient Bar Chart -->
            <div class="chart-row">
              <div class="chart-col">
                <h3 class="chart-heading">Margin Weights</h3>
                <ModelCoefficientChart
                  :coefficients="groupedModelCoefficients"
                  :intercept="activeModelData.model.intercept"
                />
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="section">
        <h2>Historical Performance</h2>
        
        <div class="controls">
          <select v-model="selectedSeason" class="select" :disabled="seasonsLoading || syncing" aria-label="Select season year">
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
              Heuristics
            </button>
            <button
              @click="viewMode = 'models'"
              class="toggle-btn"
              :class="{ active: viewMode === 'models' }"
            >
              Models
            </button>
            <button
              @click="viewMode = 'table'"
              class="toggle-btn"
              :class="{ active: viewMode === 'table' }"
            >
              Round-by-Round
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

        <div v-if="syncing" class="loading sync-message" role="status" aria-live="polite">
          <div class="spinner"></div>
          <p>Syncing historical data for {{ selectedSeason }}...</p>
        </div>
        <div v-else-if="loading" class="loading" role="status" aria-live="polite">
          <div class="spinner"></div>
        </div>
        <div v-else-if="error" class="error" role="status" aria-live="polite">
          <p>{{ error }}</p>
        </div>
        
        <!-- Summary View -->
        <div v-else-if="viewMode === 'summary' && comparison" class="comparison">
          <div v-for="entry in sortedComparison" :key="entry.heuristic" class="stat-card">
            <h3>{{ formatHeuristic(entry.heuristic) }}</h3>
            <div class="stat-grid">
              <div class="stat">
                <span class="stat-label">Accuracy</span>
                <span class="stat-value">{{ (entry.overall_accuracy * 100).toFixed(1) }}%</span>
              </div>
              <div class="stat">
                <span class="stat-label">Profit</span>
                <span class="stat-value" :class="{ positive: entry.total_profit > 0, negative: entry.total_profit < 0 }">
                  ${{ entry.total_profit.toFixed(2) }}
                </span>
              </div>
              <div class="stat">
                <span class="stat-label">Tips</span>
                <span class="stat-value">{{ entry.total_tips }}</span>
              </div>
              <div class="stat">
                <span class="stat-label">Rounds</span>
                <span class="stat-value">{{ entry.total_rounds }}</span>
              </div>
            </div>
          </div>
        </div>
        
        <!-- Models View -->
        <div v-else-if="viewMode === 'models'" class="models-section">
          <div v-if="modelComparisonLoading" class="loading" role="status" aria-live="polite">
            <div class="spinner"></div>
          </div>
          <div v-else-if="modelComparisonError" class="error" role="status" aria-live="polite">
            <p>{{ modelComparisonError }}</p>
          </div>
          <div v-else-if="modelComparison?.comparison" class="model-comparison-grid">
            <div
              v-for="entry in modelComparison.comparison"
              :key="entry.model_name"
              class="model-stat-card"
              :class="{ 'best-card': entry.model_name === modelComparison.best_overall?.model_name }"
            >
              <div class="card-header-row">
                <h3>{{ getModelDisplayName(entry.model_name) }}</h3>
                <span v-if="entry.model_name === modelComparison.best_overall?.model_name" class="best-badge">Best</span>
              </div>
              <div class="stat-grid">
                <div class="stat">
                  <span class="stat-label">Accuracy</span>
                  <span class="stat-value">{{ (entry.overall_accuracy * 100).toFixed(1) }}%</span>
                </div>
                <div class="stat">
                  <span class="stat-label">Profit</span>
                  <span class="stat-value" :class="{ positive: entry.total_profit > 0, negative: entry.total_profit < 0 }">
                    ${{ entry.total_profit.toFixed(2) }}
                  </span>
                </div>
                <div class="stat">
                  <span class="stat-label">Tips</span>
                  <span class="stat-value">{{ entry.total_tips }}</span>
                </div>
                <div class="stat">
                  <span class="stat-label">Avg Margin</span>
                  <span class="stat-value">{{ entry.avg_margin.toFixed(1) }}</span>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="empty-state">
            <p>No model data available for {{ selectedSeason }}.</p>
            <p class="empty-state-hint">Try selecting a different season or check back later.</p>
          </div>
        </div>

        <!-- Table View -->
        <div v-else-if="viewMode === 'table'" class="table-section">
          <div v-if="syncing" class="loading sync-message" role="status" aria-live="polite">
            <div class="spinner"></div>
            <p>Syncing historical data for {{ selectedSeason }}...</p>
          </div>
          <div v-else-if="tableLoading" class="loading" role="status" aria-live="polite">
            <div class="spinner"></div>
          </div>
          <div v-else-if="tableError" class="error" role="status" aria-live="polite">
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
            <p class="empty-state-hint">This could be because the season hasn't started yet, tips haven't been generated, or there are no completed games. Try selecting a different season or check back later.</p>
            <button @click="loadTableData" class="btn btn-secondary">Try Again</button>
          </div>
        </div>
        
        <!-- Charts View -->
        <div v-else-if="viewMode === 'charts'" class="charts-section">
          <div v-if="syncing" class="loading sync-message" role="status" aria-live="polite">
            <div class="spinner"></div>
            <p>Syncing historical data for {{ selectedSeason }}...</p>
          </div>
          <div v-else-if="chartsLoading" class="loading" role="status" aria-live="polite">
            <div class="spinner"></div>
          </div>
          <div v-else-if="chartsError" class="error" role="status" aria-live="polite">
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
            <p class="empty-state-hint">This could be because the season hasn't started yet, tips haven't been generated, or there are no completed games. Try selecting a different season or check back later.</p>
            <button @click="loadChartData" class="btn btn-secondary">Try Again</button>
          </div>
        </div>
  </section>
</template>

<script setup lang="ts">
import { sortByHeuristicOrder } from '~/composables/useFormatters'

const api = useApi()
const { formatHeuristic, getModelDisplayName, formatDate } = useFormatters()

// FX-05 / FX-20: page-specific SEO + canonical URL
useSeoMeta({
  title: 'Backtesting',
  description: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit, accuracy rates, and betting performance across multiple seasons.',
  keywords: 'AFL backtesting, AFL prediction accuracy, AFL betting performance, AFL tipping results, AFL profit analysis, AFL historical performance',
  ogTitle: 'Backtesting | AFL Prediction Performance & Accuracy',
  ogDescription: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit and accuracy rates.',
  ogType: 'website',
  ogUrl: 'https://whatismytip.com/backtest',
  twitterTitle: 'Backtesting | AFL Prediction Performance & Accuracy',
  twitterDescription: 'View historical performance and accuracy of our AFL prediction heuristics. Analyze year-to-date profit and accuracy rates.',
  twitterCard: 'summary_large_image',
})

useHead({
  link: [
    { rel: 'canonical', href: 'https://whatismytip.com/backtest' }
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


// FX-12: type the backtest API responses
interface ComparisonStats {
  overall_accuracy: number
  total_profit: number
  total_tips: number
  total_rounds: number
}
interface ComparisonResponse {
  comparison: Record<string, ComparisonStats>
  season: number
}
interface RoundStat {
  round_id: number
  tips_made: number
  tips_correct: number
  accuracy: number
  profit: number
}
interface HeuristicTableData {
  heuristic: string
  total_accuracy: number
  total_profit: number
  rounds: RoundStat[]
}
interface BacktestTableResponse {
  heuristics: HeuristicTableData[]
}
interface CurrentSeasonHeuristic {
  heuristic: string
  total_profit: number
  total_accuracy: number
  rounds_played: number
  avg_profit_per_round: number
  projected_annual_profit: number
}
interface CurrentSeasonResponse {
  season: number
  rounds_completed: number
  total_rounds: number
  heuristics: CurrentSeasonHeuristic[]
}

interface ModelComparisonStats {
  model_name: string
  season: number
  total_tips: number
  total_correct: number
  overall_accuracy: number
  total_profit: number
  avg_margin: number
}
interface ModelComparisonResponse {
  season: number
  comparison: ModelComparisonStats[]
  best_overall: { model_name: string; accuracy: number; profit: number }
}

interface ModelCoefficientEntry {
  feature_name: string
  coefficient: number
  model: string
  type: 'margin' | 'confidence' | 'other'
}
interface ActiveWeightedModel {
  model_name: string
  version: number
  trained_at: string | null
  training_rows: number
  intercept: number
  metrics: Record<string, number>
  coefficients: ModelCoefficientEntry[]
}
interface ActiveModelResponse {
  active: boolean
  model?: ActiveWeightedModel
  message?: string
}

const loading = ref(false)
const seasonsLoading = ref(true)
const tableLoading = ref(false)
const chartsLoading = ref(false)
const syncing = ref(false)
const currentSeasonLoading = ref(false)
const activeModelLoading = ref(false)
const modelComparisonLoading = ref(false)
const error = ref<string | null>(null)
const tableError = ref<string | null>(null)
const chartsError = ref<string | null>(null)
const modelComparisonError = ref<string | null>(null)
const currentSeasonError = ref<string | null>(null)
const activeModelError = ref<string | null>(null)
const comparison = ref<ComparisonResponse | null>(null)
const tableData = ref<BacktestTableResponse | null>(null)
const chartData = ref<{ heuristic: string; rounds: { round_id: number; profit: number; accuracy: number }[] }[] | null>(null)
const currentSeasonData = ref<CurrentSeasonResponse | null>(null)
const activeModelData = ref<ActiveModelResponse | null>(null)
const modelComparison = ref<ModelComparisonResponse | null>(null)
const viewMode = ref<'summary' | 'models' | 'table' | 'charts'>('summary')
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
    const currentYear = new Date().getFullYear()
    
    // Always show all seasons from 2010 to current year (excluding current year)
    // This ensures users can select any historical season, and backtest data
    // will be generated on-demand via the API
    availableYears.value = generateFallbackYears(currentYear)
    
    // Set default selected season to the first available year (newest)
    if (availableYears.value.length > 0) {
      selectedSeason.value = availableYears.value[0]!
    }
  } catch (e) {
    

    if (import.meta.dev) console.error('Failed to load available seasons:', e)
    // Fallback to generated years on error
    const currentYear = new Date().getFullYear()
    availableYears.value = generateFallbackYears(currentYear)
    if (availableYears.value.length > 0) {
      selectedSeason.value = availableYears.value[0]!
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
    if (import.meta.dev) console.error(e)
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
    const data = await api.getBacktestTableData(selectedSeason.value)
    if (data) {
      data.heuristics = sortByHeuristicOrder(data.heuristics)
    }
    tableData.value = data
  } catch (e) {
    tableError.value = 'Failed to load table data'
    if (import.meta.dev) console.error(e)
  } finally {
    tableLoading.value = false
    syncing.value = false
  }
}

/** Coefficients grouped by model for the display table. */
const groupedModelCoefficients = computed(() => {
  if (!activeModelData.value?.active || !activeModelData.value.model) return []
  const { coefficients } = activeModelData.value.model
  const groups: Record<string, { model: string; margin_coef: number; confidence_coef: number }> = {}

  for (const c of coefficients) {
    if (!groups[c.model]) {
      groups[c.model] = { model: c.model, margin_coef: 0, confidence_coef: 0 }
    }
    const g = groups[c.model]!
    if (c.type === 'margin') g.margin_coef = c.coefficient
    if (c.type === 'confidence') g.confidence_coef = c.coefficient
  }

  // Sort by absolute margin coefficient descending (most influential first)
  return Object.values(groups).sort(
    (a, b) => Math.abs(b.margin_coef) - Math.abs(a.margin_coef),
  )
})

/** Friendly explanation text for the active weighted model. */
const modelExplanationText = computed(() => {
  if (!activeModelData.value?.active || !activeModelData.value.model) return ''
  const m = activeModelData.value.model
  const top = groupedModelCoefficients.value.slice(0, 3)
  const topNames = top.map((r) => getModelDisplayName(r.model)).join(', ')
  const r2 = m.metrics?.r2 != null ? m.metrics.r2.toFixed(3) : 'N/A'
  return (
    `The Weighted Tip model combines all 8 ML model predictions using a ` +
    `linear regression trained on ${m.training_rows} historical games. ` +
    `Each model contributes a margin weight (influence on score margin) and a ` +
    `confidence weight (influence on confidence). ` +
    `The current model (v${m.version}) has R² = ${r2} ` +
    `with intercept ${m.intercept.toFixed(2)}. ` +
    `Most influential models: ${topNames}. ` +
    `The model is retrained weekly with updated coefficients.`
  )
})

/** Sorted comparison entries for consistent heuristic ordering in the UI. */
const sortedComparison = computed(() => {
  if (!comparison.value) return []
  const entries = Object.entries(comparison.value.comparison).map(
    ([heuristic, stats]) => ({ heuristic, ...stats }),
  )
  return sortByHeuristicOrder(entries)
})

// Watch for season changes to reload data
watch(selectedSeason, async () => {
  if (viewMode.value === 'summary') {
    await loadComparisonData()
  } else if (viewMode.value === 'models') {
    await loadModelComparisonData()
  } else if (viewMode.value === 'table') {
    await loadTableData()
  } else if (viewMode.value === 'charts') {
    await loadChartData()
  }
})

// Watch for view mode changes
watch(viewMode, async (newMode) => {
  if (newMode === 'summary' && !comparison.value) {
    await loadComparisonData()
  } else if (newMode === 'models') {
    await loadModelComparisonData()
  } else if (newMode === 'table' && !tableData.value) {
    await loadTableData()
  } else if (newMode === 'charts') {
    await loadChartData()
  }
})

const loadChartData = async () => {
  chartsLoading.value = true
  syncing.value = true
  chartsError.value = null
  
  try {
    const tableResponse = await api.getBacktestTableData(selectedSeason.value)
    // Transform table data into chart-friendly format, sorted by heuristic order
    const sorted = sortByHeuristicOrder<HeuristicTableData>(tableResponse.heuristics)
    chartData.value = sorted.map((h) => ({
      heuristic: h.heuristic,
      rounds: h.rounds.map((r: RoundStat) => ({
        round_id: r.round_id,
        profit: r.profit,
        accuracy: r.accuracy
      }))
    }))
  } catch (e) {
    chartsError.value = 'Failed to load chart data'
    if (import.meta.dev) console.error(e)
  } finally {
    chartsLoading.value = false
    syncing.value = false
  }
}

const loadActiveModelData = async () => {
  activeModelLoading.value = true
  activeModelError.value = null
  try {
    activeModelData.value = await api.getActiveModel()
  } catch (e) {
    activeModelError.value = 'Failed to load active model data'
    if (import.meta.dev) console.error(e)
  } finally {
    activeModelLoading.value = false
  }
}

const loadModelComparisonData = async () => {
  modelComparisonLoading.value = true
  modelComparisonError.value = null
  try {
    modelComparison.value = await api.compareModels(selectedSeason.value)
  } catch (e) {
    modelComparisonError.value = 'Failed to load model comparison data'
    if (import.meta.dev) console.error(e)
  } finally {
    modelComparisonLoading.value = false
  }
}

const loadCurrentSeasonData = async () => {
  currentSeasonLoading.value = true
  currentSeasonError.value = null
  
  try {
    const data = await api.getCurrentSeasonPerformance()
    if (data) {
      data.heuristics = sortByHeuristicOrder(data.heuristics)
    }
    currentSeasonData.value = data
  } catch (e) {
    currentSeasonError.value = 'Failed to load current season data'
    if (import.meta.dev) console.error(e)
  } finally {
    currentSeasonLoading.value = false
  }
}

onMounted(async () => {
  await Promise.all([
    loadAvailableSeasons(),
    loadCurrentSeasonData(),
    loadActiveModelData(),
    loadComparisonData(),
  ])
})
</script>

<style scoped>
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
  background: linear-gradient(135deg, var(--color-bg-secondary) 0%, var(--color-hover) 100%);
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

.dark .current-season-card {
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.4);
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

/* Active Weighted Model Styles */
.active-model-section {
  padding: 2.5rem 1.5rem;
  background: var(--color-bg);
  border-bottom: 2px solid var(--color-border);
}

.active-model-header {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}

.active-model-header h2 {
  margin: 0;
}

.version-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.25rem 0.625rem;
  background: #6366f1;
  color: white;
  border-radius: 1rem;
  font-size: 0.75rem;
  font-weight: 700;
}

.model-empty {
  text-align: center;
  padding: 2rem 1.5rem;
  color: var(--color-muted);
}

.model-empty-sub {
  font-size: 0.875rem;
  margin-top: 0.5rem;
  opacity: 0.7;
}

.model-content {
  max-width: 900px;
  margin: 0 auto;
}

.model-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  justify-content: center;
  margin-bottom: 1.25rem;
}

.meta-item {
  font-size: 0.8125rem;
  color: var(--color-muted);
}

.meta-item strong {
  color: var(--color-text);
}

.model-explanation {
  padding: 1rem 1.25rem;
  margin-bottom: 1.5rem;
  background: var(--color-bg-secondary);
  border-left: 3px solid #6366f1;
  border-radius: 0.375rem;
  font-size: 0.875rem;
  line-height: 1.6;
}

.coefficients-visual {
  max-width: 900px;
  margin: 0 auto;
}

/* Equation Card */
.equation-card {
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border);
  border-radius: 0.625rem;
  padding: 1.25rem;
  margin-bottom: 1.5rem;
}

.equation-title {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-muted);
  margin-bottom: 1rem;
}

.equation-display {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.25rem 0.5rem;
  font-size: 0.875rem;
  line-height: 1.8;
  font-family: 'Courier New', Courier, monospace;
}

.eq-left {
  font-weight: 700;
  color: var(--color-text);
  white-space: nowrap;
}

.eq-intercept {
  font-weight: 800;
  color: #6366f1;
  white-space: nowrap;
}

.eq-term {
  display: inline-flex;
  align-items: baseline;
  gap: 0.125rem;
  white-space: nowrap;
}

.eq-op {
  font-weight: 700;
  color: var(--color-text);
  width: 0.6em;
  text-align: center;
}

.eq-coeff {
  font-weight: 700;
  color: var(--color-text);
}

.eq-dot {
  color: var(--color-muted);
  margin: 0 0.0625rem;
}

.eq-model {
  color: var(--color-text);
}

.eq-sub {
  font-size: 0.625rem;
  color: var(--color-muted);
}

.equation-note {
  margin-top: 0.875rem;
  font-size: 0.8125rem;
  color: var(--color-muted);
  line-height: 1.5;
  border-top: 1px solid var(--color-border);
  padding-top: 0.75rem;
}

/* Chart row */
.chart-row {
  margin-top: 0.5rem;
}

.chart-col {
  width: 100%;
}

.chart-heading {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-muted);
  margin-bottom: 0.75rem;
}

/* Model Comparison (Models tab) Styles */
.models-section {
  padding: 1rem 0;
}

.model-comparison-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1rem;
}

.model-stat-card {
  border: 1px solid var(--color-border);
  padding: 1.25rem;
  border-radius: 0.375rem;
  transition: border-color 0.2s;
}

.model-stat-card.best-card {
  border-color: #6366f1;
  border-width: 2px;
}

.card-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--color-border);
}

.card-header-row h3 {
  font-size: 0.9375rem;
  font-weight: 700;
  margin: 0;
}

.best-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.1875rem 0.5rem;
  background: #6366f1;
  color: white;
  border-radius: 0.25rem;
  font-size: 0.625rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
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

  .active-model-section {
    padding: 2rem 1rem;
  }

  .model-meta-row {
    flex-direction: column;
    align-items: center;
    gap: 0.5rem;
  }

  .model-comparison-grid {
    grid-template-columns: 1fr;
  }

  .model-stat-card {
    padding: 1rem;
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

/* Disclaimer — visible on all screen sizes */
.disclaimer {
  margin-top: 1rem;
  padding: 0.75rem;
  background-color: rgba(251, 191, 36, 0.1);
  border-left: 3px solid #fbbf24;
  border-radius: 0.25rem;
}

.disclaimer small {
  color: #fbbf24;
  font-size: 0.75rem;
  line-height: 1.4;
}

/* Desktop styles */
@media (min-width: 1025px) {
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
    text-align: center;
  }

  .empty-state-hint {
    color: var(--color-muted);
    font-size: 0.875rem;
    margin: 1rem 0;
    line-height: 1.5;
  }

  .btn {
    padding: 0.625rem 1.25rem;
    border-radius: 0.375rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
    border: none;
    font-size: 0.875rem;
  }

  .btn-secondary {
    background-color: var(--color-secondary);
    color: white;
    margin-top: 1rem;
  }

  .btn-secondary:hover {
    opacity: 0.9;
  }

  .active-model-section {
    padding: 3rem 2rem;
  }

  .model-meta-row {
    gap: 1.5rem;
  }

  .model-comparison-grid {
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 1.25rem;
  }

  .model-stat-card {
    padding: 1.5rem;
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
