// LEAGUE-ROUTES (2026-09-30, user request): pure season-state derivation
// for the new first-class /{league} pages. The AFL home owns its own
// grand-final/post-season state machine (useLatestRound); state leagues
// need the equivalent end-of-season treatment derived from the plain
// `/api/events` season payload instead:
//   - 'season_complete'      → every event is settled (completed OR
//                              cancelled/void) and a current round exists
//                              → show the premier celebration
//   - 'grand_final_upcoming' → the round being displayed IS the season's
//                              highest round and still has live events
//                              → round strip shows "GF • {season}"
//   - 'regular'              → everything else (including empty payloads —
//                              an unpopulated page must never celebrate)
//
// This module is deliberately pure: no fetch, no Nuxt auto-imports, and
// only a type-import from useApi — so the vitest suite can exercise it
// without the Nuxt runtime (same pattern as useLeagueEvents.ts).

import type { SportEvent } from '~/composables/useApi'

/** End-of-season presentation state for a league home page. */
export type SeasonState = 'regular' | 'grand_final_upcoming' | 'season_complete'

/**
 * An event that will never be played to a result: finished (completed)
 * or struck from the record (cancelled/void). Matches the settled
 * semantics of `deriveCurrentRound` in useLeagueEvents.ts.
 */
function isSettled(e: SportEvent): boolean {
  return e.completed || e.status === 'cancelled' || e.status === 'void'
}

/**
 * An event that is still to be played (or in progress): dated and not
 * settled. Undated TBD fixtures are deliberately NOT live — they can't
 * anchor "the grand final is next week" on their own.
 */
function isLive(e: SportEvent): boolean {
  return !!e.starts_at && !isSettled(e)
}

/**
 * Derive the league's end-of-season state from a full season of events
 * plus the currently displayed round (as produced by
 * `deriveCurrentRound`).
 *
 * Ordering of the checks matters: season completeness wins over the
 * GF strip (a completed final round is "season complete", not "GF
 * upcoming"), and empty payloads / a missing current round degrade to
 * 'regular' instead of celebrating nothing.
 */
export function deriveSeasonState(
  events: SportEvent[],
  roundId: number | null,
): SeasonState {
  // Empty payload: nothing to celebrate (avoids the vacuous-truth trap
  // where "every event is settled" holds for zero events).
  if (events.length === 0 || roundId === null) return 'regular'

  // Season over: the whole payload is settled and we know the round to
  // anchor the celebration display on.
  if (events.every(isSettled)) return 'season_complete'

  // Grand final upcoming: the displayed round is the season's highest
  // round and still has events to be played. Requiring roundId === max
  // keeps mid-season pages 'regular' even though the back half of the
  // fixture is always "upcoming".
  const roundIds = events
    .map((e) => e.round_id)
    .filter((r): r is number => typeof r === 'number')
  if (roundIds.length === 0 || roundId !== Math.max(...roundIds)) {
    return 'regular'
  }
  const finalRoundEvents = events.filter((e) => e.round_id === roundId)
  return finalRoundEvents.some(isLive) ? 'grand_final_upcoming' : 'regular'
}

/**
 * The season premier: the `is_winner` participant of the latest-dated
 * completed event. Cancelled/void/scheduled events never count, a draw
 * (no participant flagged winner) yields null, and completed events
 * without a date can't establish "latest" — so they are ignored too.
 */
export function derivePremier(events: SportEvent[]): string | null {
  const datedCompleted = events.filter((e) => e.completed && !!e.starts_at)
  if (datedCompleted.length === 0) return null

  // Latest start time wins; undated events were already filtered out.
  const startsAtMs = (e: SportEvent): number => Date.parse(e.starts_at as string)
  const latest = datedCompleted.reduce((acc, e) =>
    startsAtMs(e) > startsAtMs(acc) ? e : acc,
  )
  return latest.participants.find((p) => p.is_winner === true)?.participant_name ?? null
}
