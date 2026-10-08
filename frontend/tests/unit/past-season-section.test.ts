/**
 * PERF-PAST-SEASON (2026-10-07, D2 of performance-per-league): the AFL
 * /performance page gains a "Most Recent Past Season" section fed by
 * /api/backtest/seasons + /api/backtest/compare?season={prev}.
 *
 * The season selection and the comparison-record -> card-rows
 * derivation are PURE functions, extracted into
 * composables/usePastSeason.ts so they pin here without a Nuxt runtime
 * (same style as season-state.test.ts).  Written before the
 * implementation existed (TDD): red first, green after.
 *
 * Contract pinned here:
 *  - selectMostRecentPastSeason: max(available_years < current_year),
 *    null when nothing qualifies; tolerates string labels ('2025',
 *    the state-league widened shape) and junk labels without throwing
 *  - summarizePastSeason: flattens the /compare response's
 *    per-heuristic record into card rows sorted by the sport's
 *    heuristic display order (Boosted Tip first, unknowns last)
 */
import { describe, it, expect } from 'vitest'
import {
  selectMostRecentPastSeason,
  summarizePastSeason,
} from '../../composables/usePastSeason'
import type { LeagueComparisonResponse, LeagueHeuristicSeasonStats } from '../../composables/useApi'

describe('selectMostRecentPastSeason', () => {
  it('picks the max year strictly below the current year', () => {
    expect(selectMostRecentPastSeason([2022, 2023, 2024, 2025, 2026], 2026)).toBe(2025)
  })

  it('returns null when only the current season exists (section stays hidden)', () => {
    expect(selectMostRecentPastSeason([2026], 2026)).toBeNull()
  })

  it('returns null for an empty year list', () => {
    expect(selectMostRecentPastSeason([], 2026)).toBeNull()
  })

  it('ignores future seasons', () => {
    expect(selectMostRecentPastSeason([2026, 2027], 2026)).toBeNull()
  })

  it('works on an unsorted list', () => {
    expect(selectMostRecentPastSeason([2024, 2021, 2023], 2026)).toBe(2024)
  })

  it('parses string season labels (state-league widened shape)', () => {
    expect(selectMostRecentPastSeason(['2023', '2024', '2025'], 2026)).toBe(2025)
  })

  it('handles a mixed number/string list', () => {
    expect(selectMostRecentPastSeason([2023, '2024'], 2026)).toBe(2024)
  })

  it('filters non-numeric junk labels instead of throwing', () => {
    expect(selectMostRecentPastSeason(['banana', '2022'], 2026)).toBe(2022)
    expect(selectMostRecentPastSeason(['banana'], 2026)).toBeNull()
  })

  it('returns null when current_year itself is unusable (defensive)', () => {
    expect(selectMostRecentPastSeason([2024, 2025], Number.NaN)).toBeNull()
  })

  // Review #2: the backend league payload carries current_year as the
  // season LABEL string — the comparison must be numeric, never
  // JS-coercion-dependent, and null must select nothing.
  it('accepts a string label current_year (league payload shape)', () => {
    expect(selectMostRecentPastSeason(['2024', '2025', '2026'], '2026')).toBe(2025)
    expect(selectMostRecentPastSeason([2024, 2025], '2026')).toBe(2025)
  })

  it('accepts a null current_year (competition with no seasons)', () => {
    expect(selectMostRecentPastSeason(['2024', '2025'], null)).toBeNull()
    expect(selectMostRecentPastSeason(['2024', '2025'], undefined)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// summarizePastSeason fixtures
// ---------------------------------------------------------------------------

/** A fully-typed per-heuristic stats entry with sensible defaults. */
function makeStats(overrides: Partial<LeagueHeuristicSeasonStats>): LeagueHeuristicSeasonStats {
  return {
    total_rounds: 20,
    total_tips: 110,
    total_correct: 66,
    overall_accuracy: 0.6,
    total_profit: 15.2,
    avg_profit_per_round: 0.76,
    best_round_accuracy: 0.9,
    worst_round_accuracy: 0.3,
    odds_coverage: 0.85,
    ...overrides,
  }
}

const COMPARISON_PAYLOAD: LeagueComparisonResponse = {
  season: 2025,
  comparison: {
    yolo: makeStats({ total_profit: -10 }),
    boosted_tip: makeStats({ total_profit: 40, overall_accuracy: 0.7 }),
    weighted_tip: makeStats({ total_profit: 25 }),
  },
  best_overall: { heuristic: 'boosted_tip', accuracy: 0.7, profit: 40 },
}

describe('summarizePastSeason', () => {
  it('returns one row per comparison entry with the heuristic name attached', () => {
    const rows = summarizePastSeason(COMPARISON_PAYLOAD)
    expect(rows).toHaveLength(3)
    const boosted = rows.find((r) => r.heuristic === 'boosted_tip')
    expect(boosted?.total_profit).toBe(40)
    expect(boosted?.overall_accuracy).toBe(0.7)
    expect(boosted?.total_rounds).toBe(20)
  })

  it('sorts rows by the heuristic display order (boosted first, unknowns last)', () => {
    const rows = summarizePastSeason({
      season: 2025,
      comparison: {
        unlisted_heuristic: makeStats({}),
        yolo: makeStats({}),
        weighted_tip: makeStats({}),
        boosted_tip: makeStats({}),
      },
      best_overall: { heuristic: null, accuracy: 0, profit: 0 },
    })
    expect(rows.map((r) => r.heuristic)).toEqual([
      'boosted_tip',
      'weighted_tip',
      'yolo',
      'unlisted_heuristic',
    ])
  })

  it('returns an empty list for an empty comparison record', () => {
    expect(
      summarizePastSeason({
        season: 2025,
        comparison: {},
        best_overall: { heuristic: null, accuracy: 0, profit: 0 },
      }),
    ).toEqual([])
  })

  it('preserves negative profit and odds coverage untouched', () => {
    const rows = summarizePastSeason(COMPARISON_PAYLOAD)
    const yolo = rows.find((r) => r.heuristic === 'yolo')
    expect(yolo?.total_profit).toBe(-10)
    expect(yolo?.odds_coverage).toBe(0.85)
  })
})
