// PERF-PAST-SEASON (2026-10-07, D2 of performance-per-league): pure
// derivations for the AFL performance page's "Most Recent Past Season"
// section.  Season selection and the comparison-record -> card-rows
// flattening are pure functions so they unit-test without a Nuxt
// runtime (same style as useSeasonState.ts).  No side effects, no
// state — the page owns the fetching and the degradation contract.
import type {
  LeagueComparisonResponse,
  LeagueHeuristicSeasonStats,
} from '~/composables/useApi'
import { sortByHeuristicOrder } from '~/composables/useFormatters'
// PERF-PER-LEAGUE: the selection rule has ONE implementation — the
// dependency-free lib/ helper shared with the league performance page.
import { mostRecentPastSeasonYear } from '~/lib/performanceSeasons'

/** One card row: the heuristic name plus its full-season stats. */
export type PastSeasonHeuristicRow = { heuristic: string } & LeagueHeuristicSeasonStats

/**
 * Pick the most recent season that is fully in the past:
 * max(available_years < currentYear), or null when nothing qualifies.
 * Delegates to lib/performanceSeasons (single tested implementation)
 * and adds the unusable-current_year guard: a NaN/undefined payload
 * must not surface a bogus season — hide the section instead.
 */
export function selectMostRecentPastSeason(
  availableYears: (number | string)[],
  currentYear: number,
): number | null {
  if (!Number.isFinite(currentYear)) return null
  return mostRecentPastSeasonYear(availableYears, currentYear)
}

/**
 * Flatten the /compare response's per-heuristic record into card rows
 * ordered by the sport's heuristic display order (Boosted Tip first,
 * unlisted heuristics sink to the end — sortByHeuristicOrder).
 */
export function summarizePastSeason(
  comparison: LeagueComparisonResponse,
): PastSeasonHeuristicRow[] {
  // `?? {}` is runtime insurance: a hand-rolled/degraded payload
  // without the comparison key yields no rows, not a crash.
  const rows: PastSeasonHeuristicRow[] = Object.entries(
    comparison.comparison ?? {},
  ).map(([heuristic, stats]) => ({ heuristic, ...stats }))
  return sortByHeuristicOrder(rows)
}
