// PERF-PER-LEAGUE (2026-10): pure season-selection helper shared by the
// performance pages (D2/D5 of the performance-per-league feature).
//
// WHY a lib/ module: exactly like lib/leagueRoutes.ts — the selection
// rule ("the most recent PAST season") is a pure contract between the
// seasons payload and the compare fetch. Keeping it dependency-free
// (no Vue, no Nuxt runtime imports) lets the pages, the vitest suite,
// and any future build-time caller share one tested implementation
// instead of re-deriving the max(year < current) selection inline.

/**
 * Pick the most recent PAST season from a `/api/backtest/seasons`
 * payload — the same `max(available_years < current_year)` rule the
 * AFL page follows (D2).
 *
 * - Season labels are four-digit-year strings for state leagues
 *   ('2025') and ints for the AFL — both are accepted.
 * - Labels that do not parse to a finite year are ignored (never
 *   throw — the payload is a boundary).
 * - Seasons at or after `currentYear` are not past seasons; when none
 *   qualifies (first season, empty payload) the caller hides the
 *   past-season section.
 *
 * Returns the selected year as a NUMBER (the `/compare?season=` query
 * param contract) or null when no past season exists.
 */
export function mostRecentPastSeasonYear(
  availableYears: (number | string)[],
  currentYear: number,
): number | null {
  const pastYears = availableYears
    .map((label) =>
      typeof label === 'number' ? label : Number.parseInt(String(label), 10),
    )
    .filter((year) => Number.isFinite(year) && year < currentYear)
  return pastYears.length > 0 ? Math.max(...pastYears) : null
}
