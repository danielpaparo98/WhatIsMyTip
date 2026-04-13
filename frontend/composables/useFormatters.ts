export const HEURISTIC_LABELS: Record<string, string> = {
  best_bet: 'Best Bet',
  yolo: 'YOLO',
  high_risk_high_reward: 'High Risk / High Reward',
}

export const MODEL_DISPLAY_NAMES: Record<string, string> = {
  elo: 'Elo Rating',
  form: 'Form',
  home_advantage: 'Home Advantage',
  value: 'Value',
}

export function useFormatters() {
  const formatHeuristic = (heuristic: string): string => {
    return HEURISTIC_LABELS[heuristic] || heuristic
  }

  const getModelDisplayName = (model: string): string => {
    return MODEL_DISPLAY_NAMES[model] || model
  }

  const formatDate = (dateStr: string): string => {
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-AU', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const formatDateShort = (dateStr: string): string => {
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-AU', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
    })
  }

  const formatTime = (dateStr: string): string => {
    const date = new Date(dateStr)
    return date.toLocaleTimeString('en-AU', {
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  return { formatHeuristic, getModelDisplayName, formatDate, formatDateShort, formatTime }
}
