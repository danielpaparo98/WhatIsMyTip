// LEAGUE-ROUTES (2026-09-30, user request): pins the pure season-state
// derivation used by the new /{league} pages. The league home must show
// the regular round strip mid-season, a "GF • {season}" strip when the
// final round is still to be played, and the premier celebration once
// every event of the season payload is completed/cancelled/void.
//
// Plain vitest imports of pure helpers (no Nuxt runtime) — same style
// as useLeagueColors.test.ts / useFormatters.test.ts.
import { describe, it, expect } from 'vitest'
import {
  deriveSeasonState,
  derivePremier,
} from '../../composables/useSeasonState'
import type { SportEvent, EventParticipant } from '../../composables/useApi'

/** Build a fully-typed SportEvent with sensible defaults. */
function makeEvent(overrides: Partial<SportEvent>): SportEvent {
  return {
    id: 1,
    slug: 'event-1',
    round_id: 1,
    venue: 'Test Oval',
    starts_at: '2026-04-01T13:10:00',
    status: 'scheduled',
    completed: false,
    competition: 'West Australian Football League',
    season: '2026',
    participants: [],
    ...overrides,
  }
}

/** A finished event: completed with a winner by default. */
function makeCompleted(
  overrides: Partial<SportEvent>,
  winner: string | null = 'Claremont',
): SportEvent {
  const participants: EventParticipant[] = [
    {
      side: 'home',
      participant_name: 'Claremont',
      score: 88,
      is_winner: winner === 'Claremont' ? true : false,
    },
    {
      side: 'away',
      participant_name: 'Peel',
      score: 55,
      is_winner: winner === 'Peel' ? true : false,
    },
  ]
  return makeEvent({ status: 'completed', completed: true, ...overrides, participants })
}

describe('deriveSeasonState', () => {
  it('returns regular for an empty event list without throwing', () => {
    expect(deriveSeasonState([], null)).toBe('regular')
  })

  it('returns regular for an empty event list even when a round id exists', () => {
    // Vacuous "everything completed" over zero events must never
    // trigger the season-complete celebration on an unpopulated page.
    expect(deriveSeasonState([], 23)).toBe('regular')
  })

  it('returns regular mid-season (current round is not the max round)', () => {
    const events = [
      makeCompleted({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00' }),
      makeCompleted({ id: 2, round_id: 5, starts_at: '2026-05-02T13:10:00' }),
      makeEvent({ id: 3, round_id: 5, starts_at: '2026-05-03T13:10:00' }),
      // The rest of the season is a scheduled fixture: the max round
      // exists but is not the current round.
      makeEvent({ id: 4, round_id: 23, starts_at: '2026-08-29T13:10:00' }),
    ]
    expect(deriveSeasonState(events, 5)).toBe('regular')
  })

  it('returns grand_final_upcoming when the current round is the max round and still live', () => {
    const events = [
      makeCompleted({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00' }),
      makeCompleted({ id: 2, round_id: 22, starts_at: '2026-08-15T13:10:00' }),
      makeEvent({ id: 3, round_id: 23, starts_at: '2026-09-26T14:10:00' }),
    ]
    expect(deriveSeasonState(events, 23)).toBe('grand_final_upcoming')
  })

  it('returns regular when the max round is live but the passed current round is earlier', () => {
    // Defensive against a stale/inconsistent round id: the GF strip is
    // only shown for the round the page is actually displaying.
    const events = [
      makeEvent({ id: 1, round_id: 5, starts_at: '2026-05-02T13:10:00' }),
      makeEvent({ id: 2, round_id: 23, starts_at: '2026-09-26T14:10:00' }),
    ]
    expect(deriveSeasonState(events, 5)).toBe('regular')
  })

  it('returns season_complete when every event is completed and a current round exists', () => {
    const events = [
      makeCompleted({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00' }),
      makeCompleted({ id: 2, round_id: 23, starts_at: '2026-09-26T14:10:00' }),
    ]
    expect(deriveSeasonState(events, 23)).toBe('season_complete')
  })

  it('treats cancelled and void events as settled for season_complete', () => {
    const events = [
      makeCompleted({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00' }),
      makeEvent({
        id: 2,
        round_id: 2,
        starts_at: '2026-04-11T13:10:00',
        status: 'cancelled',
        completed: false,
      }),
      makeEvent({
        id: 3,
        round_id: 3,
        starts_at: '2026-04-18T13:10:00',
        status: 'void',
        completed: false,
      }),
    ]
    expect(deriveSeasonState(events, 3)).toBe('season_complete')
  })

  it('does not declare season_complete while any event is still live', () => {
    const events = [
      makeCompleted({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00' }),
      makeEvent({ id: 2, round_id: 23, starts_at: '2026-09-26T14:10:00' }),
    ]
    expect(deriveSeasonState(events, 23)).not.toBe('season_complete')
  })

  it('does not declare season_complete when an unsettled event has no date', () => {
    // Undated TBD fixtures are neither completed nor cancelled/void —
    // the season is not over yet even if everything else is.
    const events = [
      makeCompleted({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00' }),
      makeEvent({ id: 2, round_id: 2, starts_at: null }),
    ]
    expect(deriveSeasonState(events, 2)).toBe('regular')
  })

  it('returns regular when all events are settled but no current round exists', () => {
    const events = [
      makeCompleted({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00' }),
    ]
    expect(deriveSeasonState(events, null)).toBe('regular')
  })

  it('ignores null-round (tournament-style) events for the max round but not for completeness', () => {
    // Grand final round upcoming while a non-round exhibition event is
    // still scheduled → neither GF-upcoming (round math ignores it as
    // the max is 23 and live there — wait, round 23 IS live) nor
    // complete (the exhibition is unsettled).
    const events = [
      makeCompleted({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00' }),
      makeEvent({ id: 2, round_id: null, starts_at: '2026-10-10T13:10:00' }),
      makeEvent({ id: 3, round_id: 23, starts_at: '2026-09-26T14:10:00' }),
    ]
    // Round 23 is the max round and it is live with roundId=23 → GF.
    expect(deriveSeasonState(events, 23)).toBe('grand_final_upcoming')

    // Same payload but the exhibition unsettled and round 23 done:
    // season is NOT complete because every event must be settled.
    const notDone = [
      makeCompleted({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00' }),
      makeEvent({ id: 2, round_id: null, starts_at: '2026-10-10T13:10:00' }),
      makeCompleted({ id: 3, round_id: 23, starts_at: '2026-09-26T14:10:00' }),
    ]
    expect(deriveSeasonState(notDone, 23)).toBe('regular')
  })
})

describe('derivePremier', () => {
  it('returns the is_winner participant of the latest-dated completed event', () => {
    const events = [
      makeCompleted(
        { id: 1, round_id: 22, starts_at: '2026-08-15T13:10:00' },
        'Peel',
      ),
      makeCompleted(
        { id: 2, round_id: 23, starts_at: '2026-09-26T14:10:00' },
        'Claremont',
      ),
    ]
    expect(derivePremier(events)).toBe('Claremont')
  })

  it('ignores cancelled/void and scheduled events when finding the latest completed', () => {
    const events = [
      makeCompleted(
        { id: 1, round_id: 22, starts_at: '2026-08-15T13:10:00' },
        'Peel',
      ),
      // Latest-dated overall, but not completed → must not win.
      makeEvent({ id: 2, round_id: 23, starts_at: '2026-09-26T14:10:00' }),
      makeEvent({
        id: 3,
        round_id: 24,
        starts_at: '2026-10-03T14:10:00',
        status: 'cancelled',
        completed: false,
      }),
    ]
    expect(derivePremier(events)).toBe('Peel')
  })

  it('returns null when the season\u2019s final round was cancelled (no false premier)', () => {
    // LEAGUE-ROUTES (2026-09-30, code review): a struck-out grand final
    // means the season ended WITHOUT a result — the latest-dated
    // COMPLETED game (here a preliminary final) must not be crowned.
    const events = [
      makeCompleted({ id: 1, round_id: 21, starts_at: '2026-08-01T13:10:00' }, 'Peel'),
      makeCompleted(
        { id: 2, round_id: 22, starts_at: '2026-09-19T14:10:00' },
        'Peel',
      ),
      makeEvent({
        id: 3,
        round_id: 23,
        starts_at: '2026-09-26T14:10:00',
        status: 'cancelled',
        completed: false,
      }),
    ]
    expect(derivePremier(events)).toBeNull()
  })

  it('returns null when the season\u2019s final round was voided', () => {
    const events = [
      makeCompleted({ id: 1, round_id: 22, starts_at: '2026-09-19T14:10:00' }, 'Peel'),
      makeEvent({
        id: 2,
        round_id: 23,
        starts_at: '2026-09-26T14:10:00',
        status: 'void',
        completed: false,
      }),
    ]
    expect(derivePremier(events)).toBeNull()
  })

  it('still derives a premier when the completed final round is the max round', () => {
    // The guard only fires on settled-but-not-completed final rounds —
    // a played grand final keeps crowning its winner.
    const events = [
      makeCompleted({ id: 1, round_id: 22, starts_at: '2026-09-19T14:10:00' }, 'Peel'),
      makeCompleted({ id: 2, round_id: 23, starts_at: '2026-09-26T14:10:00' }, 'Claremont'),
    ]
    expect(derivePremier(events)).toBe('Claremont')
  })

  it('still derives a premier when only a non-round exhibition was cancelled', () => {
    // Exhibition-style events have no round number: they can't define
    // the final round, so they don't veto the played grand final.
    const events = [
      makeCompleted({ id: 1, round_id: 23, starts_at: '2026-09-26T14:10:00' }, 'Claremont'),
      makeEvent({
        id: 2,
        round_id: null,
        starts_at: '2026-10-10T13:10:00',
        status: 'cancelled',
        completed: false,
      }),
    ]
    expect(derivePremier(events)).toBe('Claremont')
  })

  it('still derives a premier when a mid-season round was cancelled', () => {
    // Only the MAX round matters: a cancelled round 5 in an otherwise
    // completed season doesn't invalidate the grand final result.
    const events = [
      makeCompleted({ id: 1, round_id: 4, starts_at: '2026-04-25T13:10:00' }, 'Peel'),
      makeEvent({
        id: 2,
        round_id: 5,
        starts_at: '2026-05-02T13:10:00',
        status: 'cancelled',
        completed: false,
      }),
      makeCompleted({ id: 3, round_id: 23, starts_at: '2026-09-26T14:10:00' }, 'Claremont'),
    ]
    expect(derivePremier(events)).toBe('Claremont')
  })

  it('returns null when no event is completed', () => {
    const events = [
      makeEvent({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00' }),
    ]
    expect(derivePremier(events)).toBeNull()
  })

  it('returns null for an empty event list', () => {
    expect(derivePremier([])).toBeNull()
  })

  it('returns null when the latest completed event has no winner (draw)', () => {
    const draw = makeCompleted(
      { id: 1, round_id: 23, starts_at: '2026-09-26T14:10:00' },
      'Claremont',
    )
    draw.participants = draw.participants.map((p) => ({ ...p, is_winner: null }))
    expect(derivePremier([draw])).toBeNull()
  })

  it('returns null when completed events carry no date (cannot establish latest)', () => {
    const events = [
      makeCompleted({ id: 1, round_id: 23, starts_at: null }),
    ]
    expect(derivePremier(events)).toBeNull()
  })
})
