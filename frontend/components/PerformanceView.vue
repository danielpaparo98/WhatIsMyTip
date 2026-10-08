<template>
  <!-- =====================================================================
       PERFORMANCE-VIEW (2026-10-08, user request "entirely the same"):
       THE single performance view — one implementation shared by every
       league. pages/performance.vue (AFL, league="afl") and
       pages/[league]/performance.vue (league from the route) are thin
       wrappers owning only the route contract + per-route SEO; this
       component owns ALL presentation and data.

       Data source: the league-aware backtest API. AFL omits the league
       param (byte-identical legacy URLs); state leagues pass their key.
       The AFL-only model slots (weighted / boosted / model-compare) are
       fetched only for AFL today — the sections below render whenever
       those slots carry data, so a league gains them automatically the
       moment the API serves league model payloads (drop the isAfl gate
       in fetchPerformance()).
       ===================================================================== -->
  <section class="hero">
    <h1>{{ leagueConfig.displayName }}<br>Performance</h1>
    <p>How our tipping heuristics are performing.</p>
  </section>

  <div v-if="pending" class="loading" role="status" aria-live="polite">
    <div class="spinner"></div>
  </div>

  <!-- No payload at all: unsynced league / API unreachable (all degrade
       here). The approved empty state — the page never breaks. -->
  <div v-else-if="!currentSeason" class="empty" role="status" aria-live="polite">
    <p>Performance tracking isn't available for {{ leagueConfig.displayName }} yet.</p>
    <p class="empty-hint">Performance tracking begins once tipping models are live for this league.</p>
  </div>

  <template v-else>
    <!-- Most Recent Past Season: final, full-season results. ANY failure
         in the seasons→compare chain leaves the slot null and this
         section hidden — it must never break the page (degrade-quietly
         contract, same as the boosted-model slot). -->
    <section v-if="pastSeason" class="current-season-section">
      <div class="current-season-header">
        <h2>
          <span class="badge">Most Recent Past Season {{ pastSeason.season }}</span>
        </h2>
        <p class="season-progress">Final results for the complete season (not year-to-date)</p>
      </div>

      <div class="current-season-cards">
        <div
          v-for="row in pastSeason.rows"
          :key="row.heuristic"
          class="current-season-card"
        >
          <div class="card-header">
            <h3>{{ heuristicLabel(row.heuristic) }}</h3>
            <span class="heuristic-badge">Final {{ pastSeason.season }}</span>
          </div>
          <div class="card-stats">
            <div class="stat-row">
              <span class="stat-label">Final Profit</span>
              <span
                class="stat-value"
                :class="{ positive: row.total_profit > 0, negative: row.total_profit < 0 }"
              >
                ${{ row.total_profit.toFixed(2) }}
              </span>
            </div>
            <div v-if="row.total_rounds > 0" class="stat-row">
              <span class="stat-label">Accuracy</span>
              <span class="stat-value">{{ (row.overall_accuracy * 100).toFixed(1) }}%</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Rounds Played</span>
              <span class="stat-value">{{ row.total_rounds }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Tips Graded</span>
              <span class="stat-value">{{ row.total_tips }} ({{ row.total_correct }} correct)</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Odds Coverage</span>
              <span class="stat-value">{{ ((row.odds_coverage ?? 0) * 100).toFixed(0) }}%</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Current Season Section.
         m-6: the empty branch lives inside the data guard — a payload
         with no graded rounds yet renders the season-not-started state,
         not "$0.00" cards. -->
    <section class="current-season-section">
      <div class="current-season-header">
        <h2>
          <span class="badge">Current Season {{ currentSeason.season }}</span>
        </h2>
        <p class="season-progress">
          {{ currentSeason.rounds_completed }} / {{ currentSeason.total_rounds }} rounds completed
        </p>
      </div>

      <!-- Off-season / pre-Round-1 state: no graded tips yet -->
      <div v-if="!hasSeasonResults" class="season-empty">
        <p>The {{ currentSeason.season }} season hasn't started yet.</p>
        <p class="season-empty-sub">
          Performance tracking begins once Round 1 tips are graded.<template v-if="isAfl">
            Historical performance feeds the model weights shown below.</template>
        </p>
      </div>

      <template v-else>
        <!-- BT-ODDS: explain exactly how profit is settled -->
        <div class="profit-basis">
          <p>
            <strong>How profit is calculated:</strong> each tip carries a simulated
            $10 stake. Games with bookmaker odds (The Odds API) settle at the real
            decimal price for the tipped team; games without an odds snapshot settle
            at a representative $1.90; drawn games refund the stake.
            <em>Odds coverage</em> shows the share of tips settled at real prices.
          </p>
        </div>

        <div class="current-season-cards">
          <div v-for="heuristic in sortedCurrentHeuristics" :key="heuristic.heuristic" class="current-season-card">
            <div class="card-header">
              <h3>{{ heuristicLabel(heuristic.heuristic) }}</h3>
              <span class="heuristic-badge">Current Season</span>
            </div>
            <div class="card-stats">
              <div class="stat-row">
                <span class="stat-label">Year-to-Date Profit</span>
                <span class="stat-value" :class="{ positive: heuristic.total_profit > 0, negative: heuristic.total_profit < 0 }">
                  ${{ heuristic.total_profit.toFixed(2) }}
                </span>
              </div>
              <div v-if="heuristic.rounds_played > 0" class="stat-row">
                <span class="stat-label">Projected Annual Profit</span>
                <span class="stat-value projected" :class="{ positive: heuristic.projected_annual_profit > 0, negative: heuristic.projected_annual_profit < 0 }">
                  ${{ heuristic.projected_annual_profit.toFixed(2) }}
                </span>
              </div>
              <div v-if="heuristic.rounds_played > 0" class="disclaimer">
                <small>Projections are based on early season performance and may change as the season progresses.</small>
              </div>
              <div class="stat-row">
                <span class="stat-label">Accuracy</span>
                <span class="stat-value">
                  {{ heuristic.rounds_played > 0 ? (heuristic.total_accuracy * 100).toFixed(1) + '%' : '—' }}
                </span>
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
              <div class="stat-row">
                <span class="stat-label">Odds Coverage</span>
                <span class="stat-value">{{ (((heuristic.odds_coverage ?? 0) * 100)).toFixed(0) }}%</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Current Season: Model Performance (AFL model-compare slot;
             hidden entirely when the slot is null — leagues today). -->
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
      </template>
    </section>

    <!-- Active Weighted Model Section (AFL model slot — renders only
         when the league has a model source; see the header comment). -->
    <section v-if="isAfl" class="active-model-section">
      <div class="active-model-header">
        <h2>Weighted Tip Model</h2>
        <span v-if="activeModelData?.active" class="version-badge">
          v{{ activeModelData.model?.version }}
        </span>
      </div>

      <!-- The unified payload fetch carries its own top-level pending
           state, so this section has no separate loading branch — only
           error / empty / content. -->
      <div v-if="activeModelError" class="error" role="status" aria-live="polite">
        <p>{{ activeModelError }}</p>
      </div>
      <div v-if="!activeModelData?.active" class="model-empty">
        <p>No trained Weighted Tip model yet. The model will be trained after the first weekly retrain job runs.</p>
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

    <!-- Active Boosted Model (XGBoost) Section (BT-1) — AFL model slot.
         Degradation contract: both boosted endpoints map "no active
         model / no data" to null, and the slot simply never renders —
         a missing model must never break the page. -->
    <section v-if="isAfl && (boostedModelData)" class="active-model-section">
      <div class="active-model-header">
        <h2>Active Boosted Model (XGBoost)</h2>
        <span v-if="boostedModelData" class="version-badge">
          v{{ boostedModelData.version }}
        </span>
      </div>

      <div v-if="boostedModelData" class="model-content">
        <div class="model-meta-row">
          <span class="meta-item">
            <strong>Trained:</strong> {{ formatDate(boostedModelData.trained_at) }}
          </span>
          <span class="meta-item">
            <strong>Training rows:</strong> {{ boostedModelData.training_rows }}
          </span>
          <span v-if="boostedModelData.metrics.r2 != null" class="meta-item">
            <strong>R²:</strong> {{ boostedModelData.metrics.r2.toFixed(4) }}
          </span>
          <span v-if="boostedModelData.metrics.mae != null" class="meta-item">
            <strong>MAE:</strong> {{ boostedModelData.metrics.mae.toFixed(1) }}
          </span>
          <span v-if="boostedModelData.metrics.shap_base_value != null" class="meta-item">
            <strong>SHAP base value:</strong> {{ boostedModelData.metrics.shap_base_value.toFixed(2) }}
          </span>
        </div>

        <div class="model-explanation">
          <p>{{ boostedModelExplanationText }}</p>
        </div>

        <div v-if="groupedShapImportances.length > 0" class="coefficients-visual">
          <!-- SHAP Importance Bar Chart (reuses the coefficient chart) -->
          <div class="chart-row">
            <div class="chart-col">
              <h3 class="chart-heading">SHAP Feature Importance</h3>
              <ModelCoefficientChart
                :coefficients="groupedShapImportances"
                :intercept="boostedShapIntercept"
                x-axis-label="Mean |SHAP| Importance"
              />
            </div>
          </div>
        </div>
      </div>
    </section>
  </template>
</template>

<script setup lang="ts">
// ---------------------------------------------------------------------------
// PerformanceView — THE performance view for every league (and AFL).
//
// Data layer: one awaited useAsyncData per league key, so the payload
// inlines into the prerendered HTML (the SEO-C1 contract) and a route
// param change refetches with dedupe: 'cancel' (the H-5 contract).
// AFL keeps byte-identical legacy URLs by omitting the league param.
// ---------------------------------------------------------------------------
import { sortByHeuristicOrder } from '~/composables/useFormatters'
import type { ActiveBoostedModel } from '~/composables/useApi'
import { groupShapImportances } from '~/composables/useBoostedShap'
import {
  summarizePastSeason,
  type PastSeasonHeuristicRow,
} from '~/composables/usePastSeason'
import { mostRecentPastSeasonYear } from '~/lib/performanceSeasons'
import { getLeagueConfig } from '~/composables/useSportConfig'
import type {
  LeagueCurrentSeasonHeuristicPerformance,
  LeagueComparisonResponse,
} from '~/composables/useApi'

const props = defineProps<{
  /** League key ('afl' selects the legacy AFL view + model slots). */
  league: string
}>()

const isAfl = computed(() => props.league === 'afl')
const leagueConfig = computed(() => getLeagueConfig(props.league))

const api = useApi()
const { formatHeuristic, getModelDisplayName, formatDate } = useFormatters()

// FX-12: the view-level current-season shape — the AFL and league
// payloads are field-identical; only `season`'s type widens.
interface CurrentSeasonView {
  season: number | string
  rounds_completed: number
  total_rounds: number
  heuristics: LeagueCurrentSeasonHeuristicPerformance[]
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

/** Everything the view renders, resolved in one generate-time fetch. */
interface PerformancePayload {
  current: CurrentSeasonView | null
  past: { season: number | string; rows: PastSeasonHeuristicRow[] } | null
  // AFL-only model slots — null for state leagues until the API serves
  // league model payloads (then drop the isAfl gates and they light up).
  models: ModelComparisonStats[] | null
  modelsError: string | null
  activeModel: ActiveModelResponse | null
  activeModelError: string | null
  boostedModel: ActiveBoostedModel | null
}

const fetchPerformance = async (): Promise<PerformancePayload> => {
  const league = isAfl.value ? undefined : props.league

  // Current season: same shape both paths; a failure degrades to null
  // (the unavailable empty state) — a missing league never 500s.
  const current = await (isAfl.value
    ? api.getCurrentSeasonPerformance()
    : api.getLeagueCurrentSeasonPerformance(props.league)
  )
    .then((data) => {
      if (!data) return null
      return { ...data, heuristics: sortByHeuristicOrder(data.heuristics) } as CurrentSeasonView
    })
    .catch(() => null)

  // Past season: the shared max(year < current) rule over the seasons
  // payload, then the compare fetch. Additive — isolated try/catch.
  let past: PerformancePayload['past'] = null
  try {
    const seasons = await api.getLeagueSeasons(league)
    const prevSeason = mostRecentPastSeasonYear(
      seasons.available_years ?? [],
      seasons.current_year,
    )
    if (prevSeason !== null) {
      const comparison: LeagueComparisonResponse = await api.getLeagueComparison(
        league,
        prevSeason,
      )
      past = { season: comparison.season, rows: summarizePastSeason(comparison) }
    }
  } catch {
    past = null
  }

  // AFL-only model slots, each in its own try/catch so no single
  // failure can break the page (the boosted contract is "hide, never
  // error": both boosted endpoints throw on "no active model", the
  // catch nulls the slot, the section stays hidden).
  let models: PerformancePayload['models'] = null
  let modelsError: string | null = null
  let activeModel: PerformancePayload['activeModel'] = null
  let activeModelError: string | null = null
  let boostedModel: ActiveBoostedModel | null = null

  if (isAfl.value && current) {
    // The compared season comes from the server's current-season
    // payload (falls back to the client year) — a client clock near
    // New Year must not query a season with no data.
    const parsed = typeof current.season === 'number'
      ? current.season
      : Number.parseInt(String(current.season), 10)
    const season = Number.isFinite(parsed) ? parsed : new Date().getFullYear()
    try {
      const data = await api.compareModels(season)
      models = data.comparison
    } catch {
      modelsError = 'Could not load model accuracy data'
    }
    try {
      activeModel = await api.getActiveModel()
    } catch {
      activeModelError = 'Failed to load active model data'
    }
    try {
      const data = await api.getActiveBoostedModel()
      boostedModel = data.is_active ? data : null
    } catch {
      boostedModel = null
    }
  }

  return { current, past, models, modelsError, activeModel, activeModelError, boostedModel }
}

const { data, pending, refresh } = await useAsyncData<PerformancePayload>(
  computed(() => `performance-${props.league}`),
  fetchPerformance,
  { dedupe: 'cancel' },
)

// Client-side freshness on hydrated visits: the inlined payload renders
// the first paint, then ONE background refresh updates the numbers —
// no refetch during prerender/SSR (the league pages' contract; the AFL
// page gains it as part of the unification).
if (import.meta.client) {
  onNuxtReady(() => {
    void refresh()
  })
}

// ---------------------------------------------------------------------------
// View state (derived from the payload).
// ---------------------------------------------------------------------------
const currentSeason = computed(() => data.value?.current ?? null)
const pastSeason = computed(() => data.value?.past ?? null)
const currentSeasonModels = computed(() => data.value?.models ?? null)
const currentSeasonModelsError = computed(() => data.value?.modelsError ?? null)
const activeModelData = computed(() => data.value?.activeModel ?? null)
const activeModelError = computed(() => data.value?.activeModelError ?? null)
const boostedModelData = computed(() => data.value?.boostedModel ?? null)

/**
 * BT-ODDS UX: false until at least one round has been graded — drives
 * the friendly off-season empty state instead of "$0.00" cards.
 */
const hasSeasonResults = computed(() => {
  const view = currentSeason.value
  if (!view) return false
  return (
    view.rounds_completed > 0 ||
    view.heuristics.some((h) => h.rounds_played > 0)
  )
})

/** Best model name for the current season (highest accuracy). */
const currentSeasonBestModel = computed(() => {
  if (!currentSeasonModels.value || currentSeasonModels.value.length === 0) return null
  return currentSeasonModels.value.reduce((best, m) =>
    m.overall_accuracy > best.overall_accuracy ? m : best,
  ).model_name
})

// ---------------------------------------------------------------------------
// Presentation helpers (pure).
//
// Labels: the league heuristics (home_advantage / form / ladder) have no
// entry in the AFL-shaped HEURISTIC_LABELS, so they are mapped here and
// fall back to the shared formatter for anything else.
// Ordering: sortByHeuristicOrder is AFL-ordered; league heuristics are
// unlisted (a NaN-comparison edge the code review flagged) — the league
// view sorts with its own explicit, deterministic order instead.
// ---------------------------------------------------------------------------

const LEAGUE_HEURISTIC_ORDER = ['home_advantage', 'form', 'ladder']

const LEAGUE_HEURISTIC_LABELS: Record<string, string> = {
  home_advantage: 'Home Advantage',
  form: 'Form',
  ladder: 'Ladder',
}

const heuristicLabel = (heuristic: string): string =>
  LEAGUE_HEURISTIC_LABELS[heuristic] ?? formatHeuristic(heuristic)

const byLeagueHeuristicOrder = <T extends { heuristic: string }>(rows: T[]): T[] =>
  [...rows].sort(
    (a, b) =>
      LEAGUE_HEURISTIC_ORDER.indexOf(a.heuristic) -
      LEAGUE_HEURISTIC_ORDER.indexOf(b.heuristic),
  )

const sortedCurrentHeuristics = computed<LeagueCurrentSeasonHeuristicPerformance[]>(() => {
  const list = currentSeason.value?.heuristics ?? []
  return isAfl.value ? list : byLeagueHeuristicOrder(list)
})

// ---------------------------------------------------------------------------
// Active Weighted Model presentation (AFL slot).
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Active Boosted Model (XGBoost) presentation (AFL slot, BT-1).
// ---------------------------------------------------------------------------

/** Importances grouped per model for the reused coefficient chart. */
const groupedShapImportances = computed(() =>
  boostedModelData.value ? groupShapImportances(boostedModelData.value.importances) : [],
)

/** The chart requires an intercept; the SHAP base value fills that
 *  semantic slot (the component never renders it directly). */
const boostedShapIntercept = computed(() => boostedModelData.value?.metrics.shap_base_value ?? 0)

/** Friendly explanation text for the active boosted model. */
const boostedModelExplanationText = computed(() => {
  const m = boostedModelData.value
  if (!m) return ''
  const r2 = m.metrics.r2 != null ? m.metrics.r2.toFixed(3) : 'N/A'
  const mae = m.metrics.mae != null ? m.metrics.mae.toFixed(1) : 'N/A'
  const top = groupedShapImportances.value.slice(0, 3)
  const topNames = top.map((r) => getModelDisplayName(r.model)).join(', ')
  return (
    `The Boosted Tip model is a gradient-boosted tree ensemble (XGBoost) trained on ` +
    `${m.training_rows} historical games to predict the home-team-signed margin from the ` +
    `same 8 model predictions the Weighted Tip model uses. ` +
    `The current model (v${m.version}) reaches R² = ${r2} with MAE = ${mae} points and is ` +
    `retrained weekly. ` +
    `Feature importances below are mean |SHAP| values — how much each model's margin and ` +
    `confidence signal moves the prediction, with ${topNames} the most influential.`
  )
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
  line-height: 1.05;
}

.hero p {
  font-size: 1.125rem;
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
  border-radius: 0;
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
  border-radius: 0;
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
  background: transparent;
  color: var(--color-text);
  border: 1px solid var(--color-text);
  border-radius: 0;
  text-transform: uppercase;
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
  color: #15803d;
}

.stat-value.negative {
  color: #b91c1c;
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
  background: var(--color-text);
  color: var(--color-bg);
  border-radius: 0;
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
  border-left: 3px solid var(--color-text);
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
  border-radius: 0;
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
  color: var(--color-text);
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

.loading, .error, .empty {
  text-align: center;
  padding: 3rem 1.5rem;
}

.empty-hint {
  margin-top: 0.5rem;
  font-size: 0.875rem;
  color: var(--color-muted);
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
  border-radius: 0;
  padding: 0.625rem;
  text-align: center;
  background: var(--color-bg);
  transition: border-color 0.15s, box-shadow 0.15s;
}

.model-mini-card.best-model-card {
  border-color: var(--color-text);
  border-width: 2px;

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
  color: var(--color-text);
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
  color: #15803d;
}

.model-mini-profit.negative {
  color: #b91c1c;
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

  .hero p {
    font-size: 1rem;
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

  .season-empty {
    padding: 2rem 1rem;
  }

  .season-empty p {
    font-size: 1rem;
  }

  .profit-basis {
    padding: 0.75rem;
    font-size: 0.8125rem;
  }

  .active-model-section {
    padding: 2rem 1rem;
  }

  .model-meta-row {
    flex-direction: column;
    align-items: center;
    gap: 0.5rem;
  }

  .loading, .error, .empty {
    padding: 2rem 1rem;
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
}

/* Off-season / pre-Round-1 empty state */
.season-empty {
  text-align: center;
  padding: 2.5rem 1.5rem;
  color: var(--color-muted);
}

.season-empty p {
  font-size: 1.0625rem;
  font-weight: 600;
  margin-bottom: 0.375rem;
}

.season-empty-sub {
  font-size: 0.875rem;
  opacity: 0.75;
}

/* BT-ODDS: profit-settlement explainer shown above the cards */
.profit-basis {
  max-width: 900px;
  margin: 0 auto 1.25rem;
  padding: 0.875rem 1rem;
  background: var(--color-bg);
  border-left: 3px solid var(--color-text);
  border-radius: 0.25rem;
  font-size: 0.875rem;
  line-height: 1.55;
  color: var(--color-muted);
}

.profit-basis strong {
  color: var(--color-text);
}

/* Disclaimer — visible on all screen sizes */
.disclaimer {
  margin-top: 1rem;
  padding: 0.75rem;
  background-color: var(--color-bg-secondary);
  border-left: 3px solid var(--color-text);
  border-radius: 0.25rem;
}

.disclaimer small {
  color: var(--color-muted);
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

  .hero p {
    font-size: 1.25rem;
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

  .loading, .error, .empty {
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
