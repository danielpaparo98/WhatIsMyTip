// BT-1 (boosted-tip): unit tests for the pure SHAP presentation
// helpers used by the backtest page's "Active Boosted Model (XGBoost)"
// section.  The helpers live in composables/useBoostedShap.ts, kept
// framework-free precisely so they can be tested like this.
import { describe, it, expect } from 'vitest'
import {
  formatSignedPoints,
  groupShapImportances,
  maxAbsShapValue,
  shapBarPercent,
  shapFeatureLabel,
  topShapContributions,
  type ShapContributionRow,
} from '../../composables/useBoostedShap'
import { MODEL_DISPLAY_NAMES } from '../../composables/useFormatters'

const getModelDisplayName = (model: string): string => MODEL_DISPLAY_NAMES[model] ?? model

describe('shapFeatureLabel', () => {
  it('maps <model>_margin_home to "<Model> margin"', () => {
    expect(shapFeatureLabel('elo_margin_home', getModelDisplayName)).toBe('Elo Rating margin')
    expect(shapFeatureLabel('player_form_margin_home', getModelDisplayName)).toBe(
      'Player Form margin',
    )
  })

  it('maps <model>_conf to "<Model> confidence"', () => {
    expect(shapFeatureLabel('elo_conf', getModelDisplayName)).toBe('Elo Rating confidence')
    expect(shapFeatureLabel('home_advantage_conf', getModelDisplayName)).toBe(
      'Home Advantage confidence',
    )
  })

  it('produces label variants for every registered model', () => {
    for (const model of Object.keys(MODEL_DISPLAY_NAMES)) {
      const marginLabel = shapFeatureLabel(`${model}_margin_home`, getModelDisplayName)
      const confLabel = shapFeatureLabel(`${model}_conf`, getModelDisplayName)
      expect(marginLabel).toBe(`${MODEL_DISPLAY_NAMES[model]} margin`)
      expect(confLabel).toBe(`${MODEL_DISPLAY_NAMES[model]} confidence`)
      // No snake_case leaks into either rendered label.
      expect(marginLabel).not.toMatch(/_/)
      expect(confLabel).not.toMatch(/_/)
    }
  })

  it('falls back to the raw feature name for unknown conventions', () => {
    expect(shapFeatureLabel('something_else', getModelDisplayName)).toBe('something_else')
  })

  it('falls back to the raw model key when the display map lacks it', () => {
    expect(shapFeatureLabel('mystery_model_margin_home', getModelDisplayName)).toBe(
      'mystery_model margin',
    )
  })
})

describe('topShapContributions', () => {
  it('sorts by |value| descending regardless of input order', () => {
    const rows = topShapContributions({ a: 1, b: -5, c: 3 }, 3)
    expect(rows.map((r) => r.feature_name)).toEqual(['b', 'c', 'a'])
    expect(rows[0]).toEqual({ feature_name: 'b', value: -5 })
  })

  it('slices to the requested top n', () => {
    const rows = topShapContributions({ a: 1, b: -5, c: 3, d: 0.5 }, 2)
    expect(rows).toHaveLength(2)
    expect(rows.map((r) => r.feature_name)).toEqual(['b', 'c'])
  })

  it('clamps a zero/negative n to an empty list', () => {
    expect(topShapContributions({ a: 1 }, 0)).toEqual([])
    expect(topShapContributions({ a: 1 }, -3)).toEqual([])
  })

  it('returns an empty list for empty contributions', () => {
    expect(topShapContributions({}, 10)).toEqual([])
  })

  it('keeps signed values intact (no absolute-value mangling)', () => {
    const rows = topShapContributions({ x: -2.5, y: 2.5 }, 2)
    const values = rows.map((r) => r.value).sort((a, b) => a - b)
    expect(values).toEqual([-2.5, 2.5])
  })

  it('is safe against pre-sorted payloads (idempotent ordering)', () => {
    const payload = { a: 4, b: 2, c: 1 }
    const once = topShapContributions(payload, 3)
    const twice = topShapContributions(Object.fromEntries(once.map((r) => [r.feature_name, r.value])), 3)
    expect(twice.map((r) => r.feature_name)).toEqual(once.map((r) => r.feature_name))
  })
})

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

describe('formatSignedPoints', () => {
  it('prefixes positive values with an explicit plus', () => {
    expect(formatSignedPoints(1.234)).toBe('+1.23')
    expect(formatSignedPoints(0)).toBe('+0.00')
  })

  it('keeps negative values signed with a minus', () => {
    expect(formatSignedPoints(-1.234)).toBe('-1.23')
    expect(formatSignedPoints(-0.4)).toBe('-0.40')
  })
})

describe('maxAbsShapValue', () => {
  it('returns 0 for an empty list', () => {
    expect(maxAbsShapValue([])).toBe(0)
  })

  it('returns the largest absolute value', () => {
    const rows: ShapContributionRow[] = [
      { feature_name: 'a', value: -5 },
      { feature_name: 'b', value: 3 },
      { feature_name: 'c', value: 0.5 },
    ]
    expect(maxAbsShapValue(rows)).toBe(5)
  })
})

describe('shapBarPercent', () => {
  it('maps the max value to half the track (diverging centre split)', () => {
    expect(shapBarPercent(10, 10)).toBe(50)
  })

  it('scales other values proportionally', () => {
    expect(shapBarPercent(5, 10)).toBe(25)
    expect(shapBarPercent(-2.5, 10)).toBe(12.5)
  })

  it('uses the absolute value so negative bars size identically', () => {
    expect(shapBarPercent(-10, 10)).toBe(50)
  })

  it('yields 0 (never NaN) for a zero denominator', () => {
    expect(shapBarPercent(5, 0)).toBe(0)
  })
})
