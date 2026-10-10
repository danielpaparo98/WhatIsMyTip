// Tests for the sport presentation config (P4-2) — the single source
// of truth for sport-specific copy and labels.
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  AFL_CONFIG,
  LEAGUES,
  SPORT_CONFIG,
  getLeagueConfig,
  useActiveLeague,
  useSportConfig,
} from '../../composables/useSportConfig'

const MODULE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '../../composables')

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

// ---------------------------------------------------------------------------
// NRL-EXPANSION (nrl-expansion-07): the rugby-league SportConfig —
// nrl / nrlw / origin resolve to a per-competition rugby-league config
// with the reduced model set (NO AFL heuristics, NO unsourced models).
// ---------------------------------------------------------------------------

describe('rugby-league SportConfig', () => {
  const RUGBY_LEAGUE_KEYS = ['nrl', 'nrlw', 'origin']

  it('lists the three rugby-league competitions in the LEAGUES registry', () => {
    const expected: Record<string, { displayName: string; shortLabel: string }> = {
      nrl: { displayName: 'NRL', shortLabel: 'NRL' },
      nrlw: { displayName: 'NRLW', shortLabel: 'NRLW' },
      origin: { displayName: 'State of Origin', shortLabel: 'Origin' },
    }
    for (const [key, meta] of Object.entries(expected)) {
      const league = LEAGUES.find((l) => l.key === key)
      expect(league, `missing LEAGUES entry: ${key}`).toBeDefined()
      expect(league!.displayName, key).toBe(meta.displayName)
      expect(league!.shortLabel, key).toBe(meta.shortLabel)
    }
  })

  it('resolves a per-competition rugby-league config for each key', () => {
    const expected: Record<string, { displayName: string; shortLabel: string }> = {
      nrl: { displayName: 'NRL', shortLabel: 'NRL' },
      nrlw: { displayName: 'NRLW', shortLabel: 'NRLW' },
      origin: { displayName: 'State of Origin', shortLabel: 'Origin' },
    }
    for (const [key, meta] of Object.entries(expected)) {
      const config = getLeagueConfig(key)
      expect(config.sportId, key).toBe('rugby-league')
      expect(config.displayName, key).toBe(meta.displayName)
      expect(config.shortLabel, key).toBe(meta.shortLabel)
    }
  })

  it('uses rugby-league nouns (Match/Round) and the Brisbane display timezone', () => {
    for (const key of RUGBY_LEAGUE_KEYS) {
      const config = getLeagueConfig(key)
      expect(config.contestNoun, key).toBe('Match')
      expect(config.stageNoun, key).toBe('Round')
      expect(config.displayTimezone, key).toBe('Australia/Brisbane')
    }
  })

  it('orders the reduced heuristic set — elo, form, home_advantage, matchup', () => {
    for (const key of RUGBY_LEAGUE_KEYS) {
      const config = getLeagueConfig(key)
      expect(config.heuristicOrder, key).toEqual([
        'elo',
        'form',
        'home_advantage',
        'matchup',
      ])
      for (const h of config.heuristicOrder) {
        expect(config.heuristicLabels, `Missing label for heuristic: ${h}`).toHaveProperty(h)
        expect(config.heuristicLabels[h], key).not.toBe(h)
      }
    }
  })

  it('labels ONLY the reduced model set — no AFL-only or unsourced models leak in', () => {
    const models = ['elo', 'form', 'home_advantage', 'matchup']
    // Excluded per the session bundle: no NRL source for these yet, and
    // the AFL heuristics must not leak through a config spread.
    const excluded = [
      'weather_impact',
      'injury_impact',
      'player_form',
      'value',
      'boosted_tip',
      'weighted_tip',
      'best_bet',
      'yolo',
    ]
    for (const key of RUGBY_LEAGUE_KEYS) {
      const config = getLeagueConfig(key)
      for (const m of models) {
        expect(config.modelDisplayNames, `${key} missing label for model: ${m}`).toHaveProperty(m)
        expect(config.modelDisplayNames[m], key).not.toBe(m)
      }
      for (const bad of excluded) {
        expect(config.modelDisplayNames, `${key} must not list ${bad}`).not.toHaveProperty(bad)
        expect(config.heuristicOrder, `${key} must not order ${bad}`).not.toContain(bad)
      }
    }
  })

  it('caches the rugby-league configs to a stable identity', () => {
    for (const key of RUGBY_LEAGUE_KEYS) {
      expect(getLeagueConfig(key)).toBe(getLeagueConfig(key))
    }
  })

  it('stays a pure module — no Nuxt/server imports', () => {
    const source = readFileSync(resolve(MODULE_DIR, 'useSportConfig.ts'), 'utf8')
    expect(source).not.toMatch(/#imports|#app|useNuxtApp|useRuntimeConfig|\/server\//)
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
    for (const key of ['wafl', 'waflw', 'vfl', 'vflw', 'sanfl', 'aflw', 'qafl', 'qaflw', 'nwfl', 'sfl', 'nrl', 'nrlw', 'origin']) {
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

// PERFORMANCE-ROUTES (2026-10-07): the nav's Performance link stays
// inside the active league too — AFL owns the root-level /performance
// page (mirroring '/' = AFL home), every other league its own
// /{league}/performance page.
describe('useActiveLeague performancePath', () => {
  // Same module-scoped singleton caveat as homePath: each case sets its
  // own starting state explicitly.
  it('defaults to the AFL root performance page', () => {
    useActiveLeague().setActiveLeague('afl')
    const { performancePath, activeLeague } = useActiveLeague()
    expect(activeLeague.value).toBe('afl')
    expect(performancePath.value).toBe('/performance')
  })

  it('points at the league performance page for a non-AFL league', () => {
    const { setActiveLeague } = useActiveLeague()
    setActiveLeague('wafl')
    const { performancePath, activeLeague } = useActiveLeague()
    expect(activeLeague.value).toBe('wafl')
    expect(performancePath.value).toBe('/wafl/performance')
  })

  it('tracks league switches reactively', () => {
    const { performancePath, setActiveLeague } = useActiveLeague()
    setActiveLeague('vfl')
    expect(performancePath.value).toBe('/vfl/performance')
    setActiveLeague('afl')
    expect(performancePath.value).toBe('/performance')
  })

  it('every non-AFL league maps to its own performance path', () => {
    const { performancePath, setActiveLeague } = useActiveLeague()
    for (const key of ['wafl', 'waflw', 'vfl', 'vflw', 'sanfl', 'aflw', 'qafl', 'qaflw', 'nwfl', 'sfl', 'nrl', 'nrlw', 'origin']) {
      setActiveLeague(key)
      expect(performancePath.value).toBe(`/${key}/performance`)
    }
  })

  it('ignores unknown keys (path unchanged)', () => {
    useActiveLeague().setActiveLeague('afl')
    const { performancePath, setActiveLeague } = useActiveLeague()
    setActiveLeague('not-a-league')
    expect(performancePath.value).toBe('/performance')
  })

  it('is returned alongside homePath from the composable', () => {
    const paths = useActiveLeague()
    expect(paths.homePath).toBeDefined()
    expect(paths.performancePath).toBeDefined()
  })
})
