// Human-friendly labels for the 3 heuristic names returned by the
// backend.  Keep in sync with `HEURISTICS` in
// backend/scripts/seed_data.py.
export const HEURISTIC_LABELS: Record<string, string> = {
  best_bet: 'Best Bet',
  yolo: 'YOLO',
  high_risk_high_reward: 'High Risk / High Reward',
}

// Human-friendly labels for every model registered in
// `backend/packages/shared/models_ml/__init__.py`.  Previously only
// the original 4 models were mapped; the 4 newer ML models would
// render as raw snake_case keys.  CR-007 from Phase 2b.
export const MODEL_DISPLAY_NAMES: Record<string, string> = {
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
}

export function useFormatters() {
  const formatHeuristic = (heuristic: string): string => {
    return HEURISTIC_LABELS[heuristic] || heuristic
  }

  const getModelDisplayName = (model: string): string => {
    return MODEL_DISPLAY_NAMES[model] || model
  }

  // All date formatters accept `string | null | undefined` and fall
  // back to an em-dash placeholder.  Callers don't have to wrap every
  // usage in a null check.
  const formatDate = (dateStr: string | null | undefined): string => {
    if (!dateStr) return '—'
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-AU', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const formatDateShort = (dateStr: string | null | undefined): string => {
    if (!dateStr) return '—'
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-AU', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
    })
  }

  const formatTime = (dateStr: string | null | undefined): string => {
    if (!dateStr) return '—'
    const date = new Date(dateStr)
    return date.toLocaleTimeString('en-AU', {
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  return { formatHeuristic, getModelDisplayName, formatDate, formatDateShort, formatTime }
}
