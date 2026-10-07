// BT-1 (boosted-tip): pure presentation helpers for the backtest page's
// "Active Boosted Model (XGBoost)" SHAP UI — global feature importances
// only (the per-game/local SHAP card was removed by design decision).
//
// Design rationale:
// - Deliberately framework-free (no Nuxt/Vue imports) so every function
//   is unit-testable in isolation — same approach as the exported
//   `sortByHeuristicOrder` in useFormatters.ts.
// - Feature names follow the weighted-tip feature contract
//   (`<model>_margin_home` / `<model>_conf`, interleaved per model).

import type { ShapImportanceEntry } from './useApi'

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
