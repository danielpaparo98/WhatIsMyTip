// Single source of truth for sport-specific presentation (P4-2, ADR 0001).
//
// Every piece of copy, ordering, and labelling that assumes a sport
// lives HERE instead of being scattered across components.  Adding a
// sport means adding a SportConfig entry and switching on it — not
// grepping for hardcoded strings.
//
// NOTE: this module must stay dependency-free (pure constants) so it
// is importable from components, composables, and tests alike.

export interface SportConfig {
  sportId: string
  displayName: string
  /** Word for one contest: 'Game' (AFL) vs 'Match' (tennis). */
  contestNoun: string
  /** Word for a scheduled stage: 'Round' (AFL) vs 'Tournament'. */
  stageNoun: string
  /** Display timezone — pinned so prerendered HTML matches the client. */
  displayTimezone: string
  /** Heuristic display order (Weighted Tip first). */
  heuristicOrder: string[]
  heuristicLabels: Record<string, string>
  modelDisplayNames: Record<string, string>
}

/** The AFL bootstrap config — the current behaviour, made explicit. */
export const AFL_CONFIG: SportConfig = {
  sportId: 'afl',
  displayName: 'AFL',
  contestNoun: 'Game',
  stageNoun: 'Round',
  displayTimezone: 'Australia/Sydney',
  heuristicOrder: ['weighted_tip', 'best_bet', 'yolo'],
  heuristicLabels: {
    weighted_tip: 'Weighted Tip',
    best_bet: 'Best Bet',
    yolo: 'YOLO',
  },
  modelDisplayNames: {
    // Original 4
    elo: 'Elo Rating',
    form: 'Form',
    home_advantage: 'Home Advantage',
    value: 'Value',
    // Newer ML models (Phase 2: new-models-architecture)
    weather_impact: 'Weather Impact',
    injury_impact: 'Injury Impact',
    matchup: 'Matchup',
    player_form: 'Player Form',
  },
}

/** The active sport config. Multi-sport selection lands with the
 *  competition-scoped API (P4-1 follow-up); AFL until then. */
export const SPORT_CONFIG: SportConfig = AFL_CONFIG

export function useSportConfig(): SportConfig {
  return SPORT_CONFIG
}
