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

        <!-- Current Season: Model Performance -->
        <div v-if="currentSeasonModels" class="current-season-models">
          <div class="models-divider">
            <span>Individual Model Accuracy</span>
          </div>
          <div class="model-mini-grid">
            <div
              v-for="model in currentSeasonModels"
              :key="model.model_name"
              class="model-mini-card"
              :class="{ 'best-model-card': model.model_name === currentSeasonBestModel }"
            >
              <div class="model-mini-header">
                <span class="model-mini-name">{{ getModelDisplayName(model.model_name) }}</span>
                <span v-if="model.model_name === currentSeasonBestModel" class="best-dot" title="Best performing model this season">★</span>
              </div>
              <div class="model-mini-acc">
                {{ (model.overall_accuracy * 100).toFixed(1) }}%
              </div>
              <div class="model-mini-label">Accuracy</div>
              <div class="model-mini-profit" :class="{ positive: model.total_profit > 0, negative: model.total_profit < 0 }">
                ${{ model.total_profit.toFixed(0) }}
              </div>
              <div class="model-mini-label">Profit</div>
            </div>
          </div>
          <div v-if="currentSeasonModelsError" class="model-mini-error">
            <small>{{ currentSeasonModelsError }}</small>
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

const currentSeasonLoading = ref(false)
const currentSeasonError = ref<string | null>(null)
const currentSeasonData = ref<CurrentSeasonResponse | null>(null)
const currentSeasonModels = ref<ModelComparisonStats[] | null>(null)
const currentSeasonModelsError = ref<string | null>(null)
const activeModelLoading = ref(false)
const activeModelError = ref<string | null>(null)
const activeModelData = ref<ActiveModelResponse | null>(null)

/** Best model name for the current season (highest accuracy). */
const currentSeasonBestModel = computed(() => {
  if (!currentSeasonModels.value || currentSeasonModels.value.length === 0) return null
  return currentSeasonModels.value.reduce((best, m) =>
    m.overall_accuracy > best.overall_accuracy ? m : best,
  ).model_name
})

/** Coefficients grouped by model for the chart. */
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

const loadCurrentSeasonModels = async (season: number) => {
  currentSeasonModelsError.value = null
  try {
    const data = await api.compareModels(season)
    currentSeasonModels.value = data.comparison
  } catch (e) {
    currentSeasonModelsError.value = 'Could not load model accuracy data'
    if (import.meta.dev) console.error(e)
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
    loadCurrentSeasonData(),
    loadActiveModelData(),
    loadCurrentSeasonModels(new Date().getFullYear()),
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

.loading, .error {
  text-align: center;
  padding: 3rem 1.5rem;
}

/* Current Season: Model Mini-Cards */
.current-season-models {
  margin-top: 1.5rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--color-border);
}

.models-divider {
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 1rem;
}

.models-divider span {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-muted);
  padding: 0 0.75rem;
  background: var(--color-bg);
}

.model-mini-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 0.625rem;
}

.model-mini-card {
  border: 1px solid var(--color-border);
  border-radius: 0.5rem;
  padding: 0.625rem;
  text-align: center;
  background: var(--color-bg);
  transition: border-color 0.15s, box-shadow 0.15s;
}

.model-mini-card.best-model-card {
  border-color: #6366f1;
  border-width: 2px;
  box-shadow: 0 0 0 1px rgba(99, 102, 241, 0.15);
}

.model-mini-header {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.25rem;
  margin-bottom: 0.25rem;
}

.model-mini-name {
  font-size: 0.6875rem;
  font-weight: 700;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.best-dot {
  font-size: 0.6875rem;
  color: #6366f1;
  flex-shrink: 0;
}

.model-mini-acc {
  font-size: 1rem;
  font-weight: 800;
  color: var(--color-text);
  line-height: 1.2;
}

.model-mini-label {
  font-size: 0.5625rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-muted);
  margin-bottom: 0.125rem;
}

.model-mini-profit {
  font-size: 0.8125rem;
  font-weight: 700;
  line-height: 1.2;
}

.model-mini-profit.positive {
  color: #00a000;
}

.model-mini-profit.negative {
  color: #c00000;
}

.model-mini-error {
  text-align: center;
  margin-top: 0.75rem;
  color: var(--color-muted);
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

  .model-mini-grid {
    grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
    gap: 0.5rem;
  }

  .model-mini-card {
    padding: 0.5rem;
  }

  .model-mini-acc {
    font-size: 0.875rem;
  }

  .active-model-section {
    padding: 2rem 1rem;
  }

  .model-meta-row {
    flex-direction: column;
    align-items: center;
    gap: 0.5rem;
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

  .loading, .error {
    padding: 4rem 2rem;
  }

  .active-model-section {
    padding: 3rem 2rem;
  }

  .model-meta-row {
    gap: 1.5rem;
  }

}
</style>
