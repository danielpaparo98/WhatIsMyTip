/**
 * League-aware home page (2026-09-26) — repointed 2026-09-30.
 *
 * LEAGUE-ROUTES (2026-09-30, user request): pages/index.vue is
 * AFL-only again. League coverage lives in the first-class
 * pages/[league]/index.vue route; the home page carries no league
 * branch, no useLeagueEvents dependency and none of the fixture-card
 * side helpers. What remains on the home page is purely the AFL state
 * machine (grand-final week / post-season / regular round), with the
 * hero in every state (the always-hero rule).
 *
 * The pure helpers (`resolveCompetition`, `deriveCurrentRound`,
 * `sortRoundEvents`) are exercised behaviourally; page wiring is
 * asserted via source-grep, matching the repo's established
 * static-analysis test style (no Vue plugin in vitest — SFCs cannot
 * be mounted here).
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  LEAGUE_COMPETITION_NAMES,
  deriveCurrentRound,
  resolveCompetition,
  sortRoundEvents,
  type SportEvent,
} from '~/composables/useLeagueEvents'
import { LEAGUES } from '~/composables/useSportConfig'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const INDEX = readFileSync(resolve(FRONTEND_ROOT, 'pages/index.vue'), 'utf8')
const LEAGUE_PAGE = readFileSync(resolve(FRONTEND_ROOT, 'pages/[league]/index.vue'), 'utf8')
const USE_API = readFileSync(resolve(FRONTEND_ROOT, 'composables/useApi.ts'), 'utf8')
const COMPOSABLE = readFileSync(
  resolve(FRONTEND_ROOT, 'composables/useLeagueEvents.ts'),
  'utf8',
)

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const SPORTS_PAYLOAD = [
  {
    id: 'afl',
    display_name: 'Australian Football',
    competitions: [
      {
        id: 1,
        sport_id: 'afl',
        name: 'Australian Football League',
        seasons: [
          { id: 16, label: '2025', is_current: false },
          { id: 17, label: '2026', is_current: false },
        ],
      },
      {
        id: 42,
        sport_id: 'afl',
        name: 'West Australian Football League',
        seasons: [
          { id: 90, label: '2025', is_current: false },
          { id: 91, label: '2026', is_current: true },
        ],
      },
    ],
  },
]

const ev = (overrides: Partial<SportEvent> & { id: number }): SportEvent => ({
  slug: `ev${overrides.id}`,
  round_id: 1,
  venue: null,
  starts_at: null,
  status: 'scheduled',
  completed: false,
  competition: 'WAFL',
  season: '2026',
  participants: [],
  ...overrides,
})

// ---------------------------------------------------------------------------
// resolveCompetition
// ---------------------------------------------------------------------------

describe('resolveCompetition', () => {
  it('maps a league key to its competition id and current season', () => {
    // TEAM-IDENTITY (2026-09-30, user request): the resolution now also
    // surfaces the competition's sport_id so the crest population can
    // scope its GET /api/teams?sport= fetch to the league's own sport.
    expect(resolveCompetition(SPORTS_PAYLOAD, 'wafl')).toEqual({
      competitionId: 42,
      sportId: 'afl',
      seasonLabel: '2026',
    })
  })

  it('falls back to the latest season label when none is marked current', () => {
    // Production seed does not mark is_current on any season.
    const payload = structuredClone(SPORTS_PAYLOAD)
    payload[0].competitions[1].seasons.forEach(s => (s.is_current = false))
    expect(resolveCompetition(payload, 'wafl')).toEqual({
      competitionId: 42,
      sportId: 'afl',
      seasonLabel: '2026',
    })
  })

  it('returns null for the AFL key (legacy view owns it)', () => {
    expect(resolveCompetition(SPORTS_PAYLOAD, 'afl')).toBeNull()
  })

  it('returns null for a league with no synced competition', () => {
    expect(resolveCompetition(SPORTS_PAYLOAD, 'sanfl')).toBeNull()
    expect(resolveCompetition(SPORTS_PAYLOAD, 'tsl')).toBeNull()
  })

  it('returns null for a null/empty sports payload', () => {
    expect(resolveCompetition(null, 'wafl')).toBeNull()
    expect(resolveCompetition([], 'wafl')).toBeNull()
  })

  it('covers every non-AFL league in the selector', () => {
    const selectable = LEAGUES.map(l => l.key).filter(k => k !== 'afl')
    for (const key of selectable) {
      expect(LEAGUE_COMPETITION_NAMES[key], `missing name for ${key}`).toBeTruthy()
    }
  })
})

// ---------------------------------------------------------------------------
// deriveCurrentRound
// ---------------------------------------------------------------------------

describe('deriveCurrentRound', () => {
  const NOW = new Date('2026-08-15T00:00:00Z')

  it('returns null for an empty or round-less event list', () => {
    expect(deriveCurrentRound([], NOW)).toBeNull()
    expect(deriveCurrentRound([ev({ id: 1, round_id: null })], NOW)).toBeNull()
  })

  it('picks the earliest round that still has upcoming events', () => {
    const events = [
      ev({ id: 1, round_id: 18, starts_at: '2026-08-16T05:00:00Z' }), // upcoming
      ev({ id: 2, round_id: 19, starts_at: '2026-08-23T05:00:00Z' }), // later
      ev({ id: 3, round_id: 17, starts_at: '2026-08-09T05:00:00Z', completed: true, status: 'completed' }),
    ]
    expect(deriveCurrentRound(events, NOW)).toBe(18)
  })

  it('falls back to the latest-played round once the season is over', () => {
    const events = [
      ev({ id: 1, round_id: 20, starts_at: '2026-08-30T05:00:00Z', completed: true, status: 'completed' }),
      ev({ id: 2, round_id: 19, starts_at: '2026-08-23T05:00:00Z', completed: true, status: 'completed' }),
    ]
    expect(deriveCurrentRound(events, NOW)).toBe(20)
  })

  it('ignores cancelled/void events when finding upcoming rounds', () => {
    const events = [
      ev({ id: 1, round_id: 18, starts_at: '2026-08-16T05:00:00Z', status: 'cancelled' }),
      ev({ id: 2, round_id: 17, starts_at: '2026-08-09T05:00:00Z', completed: true, status: 'completed' }),
    ]
    expect(deriveCurrentRound(events, NOW)).toBe(17)
  })

  it('undated events do not break the derivation', () => {
    const events = [
      ev({ id: 1, round_id: 18, starts_at: null }),
      ev({ id: 2, round_id: 17, starts_at: '2026-08-09T05:00:00Z', completed: true, status: 'completed' }),
    ]
    // No dated upcoming events → latest-played round wins.
    expect(deriveCurrentRound(events, NOW)).toBe(17)
  })
})

// ---------------------------------------------------------------------------
// sortRoundEvents
// ---------------------------------------------------------------------------

describe('sortRoundEvents', () => {
  it('orders events by start time, undated last, without mutating input', () => {
    const a = ev({ id: 1, starts_at: '2026-08-16T05:00:00Z' })
    const b = ev({ id: 2, starts_at: '2026-08-15T05:00:00Z' })
    const c = ev({ id: 3, starts_at: null })
    const input = [a, b, c]
    const sorted = sortRoundEvents(input)
    expect(sorted.map(e => e.id)).toEqual([2, 1, 3])
    expect(input.map(e => e.id)).toEqual([1, 2, 3])
  })
})

// ---------------------------------------------------------------------------
// Page wiring — source-grep assertions
// ---------------------------------------------------------------------------

describe('home page wiring (source-grep)', () => {
  it('renders the hero in the post-season branch (always-hero rule)', () => {
    // A hero section must sit between the post-season branch opening and
    // the celebration component (previously the celebration rendered bare).
    const postBranchIdx = INDEX.indexOf('v-else-if="isPostSeason"')
    const celebrationIdx = INDEX.indexOf('<OffSeasonCelebration')
    expect(postBranchIdx).toBeGreaterThan(-1)
    expect(celebrationIdx).toBeGreaterThan(postBranchIdx)
    const heroAfterBranch = INDEX.indexOf('class="hero"', postBranchIdx)
    expect(heroAfterBranch).toBeGreaterThan(-1)
    expect(heroAfterBranch).toBeLessThan(celebrationIdx)
  })

  it('is AFL-only again — the grand-final state owns the first branch', () => {
    // LEAGUE-ROUTES (2026-09-30, user request): the league branch is
    // gone and the page is purely the AFL state machine. With the old
    // STATE 0 removed, the grand-final branch is the FIRST branch
    // (v-if) again; post-season and regular remain else-branches.
    // Exactly three heroes — one per AFL state.
    expect(INDEX).toMatch(/v-if="isGrandFinal"/)
    expect(INDEX).toMatch(/v-else-if="isPostSeason"/)
    expect(INDEX).not.toMatch(/!isAflLeague/)
    expect(INDEX.match(/class="hero"/g)?.length).toBe(3)
  })

  it('carries no league-view code (no composable, no side helpers)', () => {
    // The league branch, its useLeagueEvents() wiring, the side*
    // fixture-card helpers and the now-dead league styles all moved to
    // pages/[league]/index.vue.
    expect(INDEX).not.toMatch(/useLeagueEvents/)
    expect(INDEX).not.toMatch(/useActiveLeague/)
    expect(INDEX).not.toMatch(/\bsideOf\b|\bsideName\b|\bsideScore\b|\bsideIsWinner\b/)
    expect(INDEX).not.toMatch(/leaguePending|leagueRound\b|leagueEvents\b|league-result/)
  })

  it('league coverage lives in pages/[league]/index.vue', () => {
    // The dedicated route is the single league home: route-driven
    // composable call, fixture cards with participant helpers, and the
    // monochrome result rows.
    expect(LEAGUE_PAGE).toMatch(/useLeagueEvents\(leagueKey\)/)
    expect(LEAGUE_PAGE).toMatch(/\bsideName\b/)
    expect(LEAGUE_PAGE).toMatch(/roundEvents/)
    expect(LEAGUE_PAGE).toMatch(/participant_name/)
    expect(LEAGUE_PAGE).toMatch(/league-result/)
  })
})

// ---------------------------------------------------------------------------
// useApi contract for the league read side
// ---------------------------------------------------------------------------

describe('useApi league additions', () => {
  it('exposes getSports and getEvents', () => {
    expect(USE_API).toMatch(/getSports/)
    expect(USE_API).toMatch(/getEvents/)
    expect(USE_API).toMatch(/\/api\/sports/)
    expect(USE_API).toMatch(/\/api\/events\?/)
  })

  it('declares the event payload types mirroring the backend schema', () => {
    expect(USE_API).toMatch(/interface SportEvent[\s\S]*?round_id/)
    expect(USE_API).toMatch(/interface EventParticipant[\s\S]*?participant_name/)
    expect(COMPOSABLE).toMatch(/LEAGUE_COMPETITION_NAMES/)
  })
})
