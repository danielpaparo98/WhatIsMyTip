// BT-1 (boosted-tip): pure presentation helpers for the backtest page's
// "Active Boosted Model (XGBoost)" SHAP UI.
//
// Design rationale:
// - Deliberately framework-free (no Nuxt/Vue imports) so every function
//   is unit-testable in isolation — same approach as the exported
//   `sortByHeuristicOrder` in useFormatters.ts.
// - Feature names follow the weighted-tip feature contract
//   (`<model>_margin_home` / `<model>_conf`, interleaved per model);
//   labels are resolved through an INJECTED display-name resolver so
//   this module has no composable dependency and stays pure.
// - The per-game contributions payload is documented as pre-sorted by
//   |value| descending, but we never trust transport order — re-sorting
//   defensively keeps the card correct even if the contract loosens.

import type { ShapImportanceEntry } from './useApi'

/** Feature-name suffix for a model's home-signed margin prediction. */
export const MARGIN_FEATURE_SUFFIX = '_margin_home'

/** Feature-name suffix for a model's confidence prediction. */
export const CONF_FEATURE_SUFFIX = '_conf'

/** One signed per-game SHAP contribution row, sorted by |value| desc. */
export interface ShapContributionRow {
  feature_name: string
  value: number
}

/**
 * Per-model row fed to ModelCoefficientChart — structurally identical
 * to the component's `CoefficientRow` ({ model, margin_coef,
 * confidence_coef }) so the chart can be reused unchanged.
 */
export interface ShapImportanceChartRow {
  model: string
  margin_coef: number
  confidence_coef: number
}

/**
 * Map a raw SHAP feature name to a human label using the repo's
 * model-display-name convention:
 *   `elo_margin_home` -> "Elo Rating margin"
 *   `elo_conf`        -> "Elo Rating confidence"
 * Names matching neither convention pass through untouched.
 */
export function shapFeatureLabel(
  featureName: string,
  getModelDisplayName: (model: string) => string,
): string {
  if (featureName.endsWith(MARGIN_FEATURE_SUFFIX)) {
    const model = featureName.slice(0, -MARGIN_FEATURE_SUFFIX.length)
    return `${getModelDisplayName(model)} margin`
  }
  if (featureName.endsWith(CONF_FEATURE_SUFFIX)) {
    const model = featureName.slice(0, -CONF_FEATURE_SUFFIX.length)
    return `${getModelDisplayName(model)} confidence`
  }
  return featureName
}

/**
 * Take a per-game `contributions` map (feature name -> signed SHAP
 * value) and return the top `n` rows by |value| descending.  `n` is
 * clamped at 0 so a bad constant degrades to an empty list, not a
 * crash.  Ties keep Object.entries insertion order (stable sort).
 */
export function topShapContributions(
  contributions: Record<string, number>,
  n: number,
): ShapContributionRow[] {
  return Object.entries(contributions)
    .map(([feature_name, value]) => ({ feature_name, value }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, Math.max(0, n))
}

/**
 * Group global SHAP importances (16 interleaved features) into one row
 * per underlying model for ModelCoefficientChart: margin features fill
 * `margin_coef`, confidence features fill `confidence_coef`, and any
 * future non-conforming type ('other') is conservatively treated as a
 * margin signal so its magnitude still shows.  Output is sorted by
 * combined magnitude descending — matching the chart's own internal
 * sort, so y-axis order is deterministic regardless of input order.
 */
export function groupShapImportances(
  importances: ShapImportanceEntry[],
): ShapImportanceChartRow[] {
  const groups: Record<string, ShapImportanceChartRow> = {}
  for (const imp of importances) {
    const row = groups[imp.model] ?? { model: imp.model, margin_coef: 0, confidence_coef: 0 }
    if (imp.type === 'confidence') row.confidence_coef = imp.shap_value
    else row.margin_coef = imp.shap_value
    groups[imp.model] = row
  }
  return Object.values(groups).sort(
    (a, b) =>
      Math.abs(b.margin_coef + b.confidence_coef) -
      Math.abs(a.margin_coef + a.confidence_coef),
  )
}

/**
 * Signed points formatter for SHAP display: positive values get an
 * explicit "+" (they push the prediction toward the home team) —
 * same convention as ModelCoefficientChart's tooltip.
 */
export function formatSignedPoints(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}`
}

/**
 * Largest |value| across contribution rows — the bar-list scaling
 * denominator.  Empty input yields 0 (callers must guard division).
 */
export function maxAbsShapValue(rows: ShapContributionRow[]): number {
  return rows.reduce((max, r) => Math.max(max, Math.abs(r.value)), 0)
}

/**
 * Bar width percentage for the diverging per-game contribution bars.
 * The track is split at its centre, so |maxAbs| maps to 50% of the
 * track and every other bar scales proportionally.  A zero denominator
 * yields 0 (no bar) rather than Infinity/NaN.
 */
export function shapBarPercent(value: number, maxAbs: number): number {
  if (maxAbs <= 0) return 0
  return (Math.abs(value) / maxAbs) * 50
}
