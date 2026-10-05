// Tests for the sport presentation config (P4-2) — the single source
// of truth for sport-specific copy and labels.
import { describe, expect, it } from 'vitest'
import {
  AFL_CONFIG,
  SPORT_CONFIG,
  useActiveLeague,
  useSportConfig,
} from '../../composables/useSportConfig'

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

  // BT-1 (boosted-tip): four heuristics now — boosted_tip leads as the
  // flagship; best_bet is retained for historical-season display.
  it('labels all four heuristics in boosted-tip-first order', () => {
    expect(SPORT_CONFIG.heuristicOrder).toEqual([
      'boosted_tip',
      'weighted_tip',
      'best_bet',
      'yolo',
    ])
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

// LEAGUE-NAV (2026-09-30, user request): the nav's home/Tips links stay
// inside the active league — homePath derives the league home URL.
describe('useActiveLeague homePath', () => {
  // The store is module-scoped (singleton across imports), so each case
  // sets its own starting state explicitly rather than assuming order.
  it('defaults to the AFL root', () => {
    useActiveLeague().setActiveLeague('afl')
    const { homePath, activeLeague } = useActiveLeague()
    expect(activeLeague.value).toBe('afl')
    expect(homePath.value).toBe('/')
  })

  it('points at the league home for a non-AFL league', () => {
    const { setActiveLeague } = useActiveLeague()
    setActiveLeague('wafl')
    const { homePath, activeLeague } = useActiveLeague()
    expect(activeLeague.value).toBe('wafl')
    expect(homePath.value).toBe('/wafl')
  })

  it('tracks league switches reactively', () => {
    const { homePath, setActiveLeague } = useActiveLeague()
    setActiveLeague('vfl')
    expect(homePath.value).toBe('/vfl')
    setActiveLeague('afl')
    expect(homePath.value).toBe('/')
  })

  it('every non-AFL league maps to its own home path', () => {
    const { homePath, setActiveLeague } = useActiveLeague()
    for (const key of ['wafl', 'waflw', 'vfl', 'vflw', 'sanfl', 'aflw', 'qafl', 'qaflw', 'nwfl', 'sfl']) {
      setActiveLeague(key)
      expect(homePath.value).toBe(`/${key}`)
    }
  })

  it('ignores unknown keys (path unchanged)', () => {
    useActiveLeague().setActiveLeague('afl')
    const { homePath, setActiveLeague } = useActiveLeague()
    setActiveLeague('not-a-league')
    expect(homePath.value).toBe('/')
  })
})
