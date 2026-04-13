export const HEURISTIC_CHART_COLORS: Record<string, { border: string; background: string }> = {
  best_bet: {
    border: '#3b82f6',
    background: 'rgba(59, 130, 246, 0.8)',
  },
  high_risk_high_reward: {
    border: '#f97316',
    background: 'rgba(249, 115, 22, 0.8)',
  },
  yolo: {
    border: '#ef4444',
    background: 'rgba(239, 68, 68, 0.8)',
  },
}

export const HEURISTIC_CHART_LABELS: Record<string, string> = {
  best_bet: 'Best Bet',
  yolo: 'YOLO',
  high_risk_high_reward: 'High Risk / High Reward',
}

export const DEFAULT_CHART_COLORS = {
  border: '#6b7280',
  background: 'rgba(107, 114, 128, 0.8)',
}

export function useChartTheme() {
  const getHeuristicColors = (heuristic: string, backgroundOpacity = 0.8) => {
    const colors = HEURISTIC_CHART_COLORS[heuristic]
    if (!colors) return DEFAULT_CHART_COLORS

    // Allow customizing background opacity by replacing the alpha value
    const borderRgb = colors.border
    const bgMatch = colors.background.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/)
    if (bgMatch) {
      return {
        border: borderRgb,
        background: `rgba(${bgMatch[1]}, ${bgMatch[2]}, ${bgMatch[3]}, ${backgroundOpacity})`,
      }
    }
    return colors
  }

  const getHeuristicLabel = (heuristic: string): string => {
    return HEURISTIC_CHART_LABELS[heuristic] || heuristic
  }

  return { getHeuristicColors, getHeuristicLabel, HEURISTIC_CHART_COLORS, HEURISTIC_CHART_LABELS }
}
