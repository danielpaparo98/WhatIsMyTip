// NRL-TIPS (nrl-expansion-09, 2026-10-10): pure presentation helpers for
// the three rugby-league tips pages (pages/nrl, pages/nrlw, pages/origin).
//
// This module is deliberately dependency-light (no Nuxt auto-imports, no
// fetch) so the vitest suite can exercise the label/wording contracts
// without mounting the SFC — the repo's established pattern for helpers
// a component consumes (useLeagueMatchRoute.ts, lib/leagueRoutes.ts).
//
// The ONLY source of heuristic/model labels, the contest noun and the
// stage noun is the league's SportConfig (getLeagueConfig(key) resolves
// RUGBY_LEAGUE_CONFIG for these keys — nrl-expansion-07/08 contract).
// Nothing here hardcodes a label: the config maps flow through verbatim.

import { RUGBY_LEAGUE_CONFIG, getLeagueConfig } from './useSportConfig'
import type { SeasonState } from './useSeasonState'

/** One tipping model as the tips surface renders it: registry key + label. */
export interface LeagueTipModel {
  key: string
  label: string
}

/**
 * Resolve the model set a rugby-league tips page presents, in the
 * config's display order, with each registry key mapped through the
 * config's label map. A key without a label falls back to the raw key —
 * a registry/label drift degrades visibly instead of vanishing.
 */
export function tipModels(
  order: readonly string[],
  labels: Record<string, string>,
): LeagueTipModel[] {
  return order.map((key) => ({ key, label: labels[key] ?? key }))
}

/**
 * The round-strip wording: "{stageNoun} {n} • {season}" for a regular
 * round, "GF • {season}" once the season's final round is reached
 * (upcoming or played — the league-page convention), and '' when no
 * round exists yet (the strip renders conditionally on a non-empty
 * value). Pure so the wording contract is testable without the SFC.
 */
export function formatRoundStrip(
  roundId: number | null,
  seasonLabel: string | null,
  seasonState: SeasonState,
  stageNoun: string,
): string {
  if (roundId === null) return ''
  const season = seasonLabel ?? ''
  return seasonState === 'regular'
    ? `${stageNoun} ${roundId} • ${season}`
    : `GF • ${season}`
}

/**
 * The route contract for this view: exactly the competition keys whose
 * SportConfig resolves to the rugby-league sport. Any other key is a
 * caller bug — the view degrades to its unavailable state instead of
 * presenting one league's models under another's name.
 */
export function isRugbyLeagueLeague(key: string | null | undefined): boolean {
  if (!key) return false
  return getLeagueConfig(key).sportId === RUGBY_LEAGUE_CONFIG.sportId
}
