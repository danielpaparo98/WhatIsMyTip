// Tests for the sport presentation config (P4-2) — the single source
// of truth for sport-specific copy and labels.
import { describe, expect, it } from 'vitest'
import { AFL_CONFIG, SPORT_CONFIG, useSportConfig } from '../../composables/useSportConfig'

describe('SPORT_CONFIG', () => {
  it('defaults to the AFL bootstrap config', () => {
    expect(SPORT_CONFIG).toBe(AFL_CONFIG)
    expect(SPORT_CONFIG.sportId).toBe('afl')
  })

  it('useSportConfig returns the active config', () => {
    expect(useSportConfig()).toBe(SPORT_CONFIG)
  })

  it('labels every model with a human-readable name', () => {
    const models = [
      'elo',
      'form',
      'home_advantage',
      'value',
      'weather_impact',
      'injury_impact',
      'matchup',
      'player_form',
    ]
    for (const m of models) {
      expect(SPORT_CONFIG.modelDisplayNames, `Missing label for model: ${m}`).toHaveProperty(m)
      expect(SPORT_CONFIG.modelDisplayNames[m]).not.toBe(m)
    }
  })

  it('labels all three heuristics in weighted-tip-first order', () => {
    expect(SPORT_CONFIG.heuristicOrder).toEqual(['weighted_tip', 'best_bet', 'yolo'])
    for (const h of SPORT_CONFIG.heuristicOrder) {
      expect(SPORT_CONFIG.heuristicLabels, `Missing label for heuristic: ${h}`).toHaveProperty(h)
    }
  })

  it('carries sport nouns and a display timezone', () => {
    expect(SPORT_CONFIG.contestNoun).toBe('Game')
    expect(SPORT_CONFIG.stageNoun).toBe('Round')
    expect(SPORT_CONFIG.displayTimezone).toMatch(/^[A-Za-z]+\/[A-Za-z_]+$/)
  })
})
