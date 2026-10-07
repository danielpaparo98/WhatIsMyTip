// BT-1 (boosted-tip): unit tests for the GLOBAL SHAP presentation
// helpers used by the backtest page's "Active Boosted Model (XGBoost)"
// section.  The per-game/local SHAP card was removed by design decision
// — only the global feature-importance chart remains.
import { describe, it, expect } from 'vitest'
import { groupShapImportances } from '../../composables/useBoostedShap'

describe('groupShapImportances', () => {
  const entry = (
    feature_name: string,
    shap_value: number,
    model: string,
    type: 'margin' | 'confidence' | 'other',
  ) => ({ feature_name, shap_value, model, type })

  it('routes margin features to margin_coef and confidence features to confidence_coef', () => {
    const rows = groupShapImportances([
      entry('elo_margin_home', 0.7, 'elo', 'margin'),
      entry('elo_conf', 0.3, 'elo', 'confidence'),
    ])
    expect(rows).toEqual([{ model: 'elo', margin_coef: 0.7, confidence_coef: 0.3 }])
  })

  it('merges multiple entries for the same model into one row', () => {
    const rows = groupShapImportances([
      entry('form_margin_home', 1.0, 'form', 'margin'),
      entry('form_conf', 0.2, 'form', 'confidence'),
      entry('elo_margin_home', 0.5, 'elo', 'margin'),
    ])
    expect(rows).toHaveLength(2)
    const form = rows.find((r) => r.model === 'form')
    expect(form).toEqual({ model: 'form', margin_coef: 1.0, confidence_coef: 0.2 })
  })

  it('treats an unknown type as a margin signal so magnitude still shows', () => {
    const rows = groupShapImportances([entry('mystery_feature', 0.9, 'mystery', 'other')])
    expect(rows).toEqual([{ model: 'mystery', margin_coef: 0.9, confidence_coef: 0 }])
  })

  it('sorts by combined magnitude descending', () => {
    const rows = groupShapImportances([
      entry('a', 0.1, 'small', 'margin'),
      entry('b', 2.0, 'big', 'margin'),
      entry('c', 1.0, 'middle', 'margin'),
    ])
    expect(rows.map((r) => r.model)).toEqual(['big', 'middle', 'small'])
  })

  it('returns an empty list for empty importances', () => {
    expect(groupShapImportances([])).toEqual([])
  })
})
