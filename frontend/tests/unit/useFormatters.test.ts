import { describe, it, expect } from 'vitest'
import {
  HEURISTIC_LABELS,
  MODEL_DISPLAY_NAMES,
  useFormatters,
} from '../../composables/useFormatters'

// The model registry currently exposes 8 model names via
// backend/packages/shared/models_ml/__init__.py:
//   elo, form, home_advantage, value, weather_impact,
//   injury_impact, matchup, player_form
// But MODEL_DISPLAY_NAMES only had the original 4 — the other 4
// would render as raw snake_case keys.  Same for HEURISTIC_LABELS.

const EXPECTED_MODELS = [
  'elo',
  'form',
  'home_advantage',
  'value',
  'weather_impact',
  'injury_impact',
  'matchup',
  'player_form',
] as const

const EXPECTED_HEURISTICS = [
  'best_bet',
  'yolo',
  'weighted_tip',
] as const

describe('MODEL_DISPLAY_NAMES', () => {
  it('covers every backend model name with a human-friendly label', () => {
    for (const m of EXPECTED_MODELS) {
      expect(MODEL_DISPLAY_NAMES, `Missing label for model: ${m}`).toHaveProperty(m)
      // The label must be different from the raw key (otherwise the
      // mapping is a no-op and the user sees "weather_impact" on the page).
      expect(MODEL_DISPLAY_NAMES[m]).not.toBe(m)
      // Must not be empty.
      expect(MODEL_DISPLAY_NAMES[m].length).toBeGreaterThan(0)
    }
  })

  it('uses Title Case for every label (no snake_case keys leak through)', () => {
    for (const m of EXPECTED_MODELS) {
      const label = MODEL_DISPLAY_NAMES[m]
      // No underscores in the rendered label (the only requirement —
      // some labels are intentionally single-word like "Form" or
      // "Matchup").
      expect(label, `Label for ${m} contains underscores`).not.toMatch(/_/)
    }
  })
})

describe('HEURISTIC_LABELS', () => {
  it('covers every backend heuristic name', () => {
    for (const h of EXPECTED_HEURISTICS) {
      expect(HEURISTIC_LABELS, `Missing label for heuristic: ${h}`).toHaveProperty(h)
      expect(HEURISTIC_LABELS[h]).not.toBe(h)
      expect(HEURISTIC_LABELS[h].length).toBeGreaterThan(0)
    }
  })

  it('maps weighted_tip to the "Weighted Tip" human label', () => {
    expect(HEURISTIC_LABELS.weighted_tip).toBe('Weighted Tip')
  })
})

describe('useFormatters().getModelDisplayName', () => {
  const { getModelDisplayName } = useFormatters()

  it('returns a human label for every known model', () => {
    for (const m of EXPECTED_MODELS) {
      const label = getModelDisplayName(m)
      expect(label).not.toBe(m)
      expect(label.length).toBeGreaterThan(0)
    }
  })

  it('falls back to the raw input for unknown models (no crash)', () => {
    expect(getModelDisplayName('mystery_model')).toBe('mystery_model')
  })
})

describe('useFormatters().formatHeuristic', () => {
  const { formatHeuristic } = useFormatters()

  it('returns a human label for every known heuristic', () => {
    for (const h of EXPECTED_HEURISTICS) {
      const label = formatHeuristic(h)
      expect(label).not.toBe(h)
      expect(label.length).toBeGreaterThan(0)
    }
  })

  it('falls back to the raw input for unknown heuristics', () => {
    expect(formatHeuristic('mystery_heuristic')).toBe('mystery_heuristic')
  })
})
