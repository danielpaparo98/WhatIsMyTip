/**
 * League-aware home page (2026-09-26, user request).
 *
 *  1. The hero renders in EVERY home-page state — grand-final week,
 *     post-season AND the new per-league view (the post-season
 *     celebration previously rendered without it).
 *  2. Selecting a state league (WAFL, VFL, …) in the header swaps the
 *     home page to that league's own current round of fixtures,
 *     independently of whatever the AFL state machine is doing.
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
    expect(resolveCompetition(SPORTS_PAYLOAD, 'wafl')).toEqual({
      competitionId: 42,
      seasonLabel: '2026',
    })
  })

  it('falls back to the latest season label when none is marked current', () => {
    // Production seed does not mark is_current on any season.
    const payload = structuredClone(SPORTS_PAYLOAD)
    payload[0].competitions[1].seasons.forEach(s => (s.is_current = false))
    expect(resolveCompetition(payload, 'wafl')).toEqual({
      competitionId: 42,
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
    // League, grand-final, post-season AND regular branches each carry a hero.
    expect(INDEX.match(/class="hero"/g)?.length).toBeGreaterThanOrEqual(4)
  })

  it('routes non-AFL leagues to a dedicated league view ahead of the AFL states', () => {
    // The league branch is the FIRST branch and keyed off the active league.
    expect(INDEX).toMatch(/v-if="!isAflLeague"/)
    // The AFL states become else-branches.
    expect(INDEX).toMatch(/v-else-if="isGrandFinal"/)
    expect(INDEX).toMatch(/v-else-if="isPostSeason"/)
  })

  it('consumes the shared active-league composable', () => {
    expect(INDEX).toMatch(/useActiveLeague\(\)/)
    expect(INDEX).toMatch(/useLeagueEvents\(\)/)
  })

  it('renders fixture cards with participants, venue and date', () => {
    expect(INDEX).toMatch(/roundEvents/)
    expect(INDEX).toMatch(/participant_name|homeParticipant|homeSide/)
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
