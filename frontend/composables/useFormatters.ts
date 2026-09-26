// Human-friendly labels and sport-specific presentation, sourced from
// the single sport config (P4-2).  The named exports below are kept
// for backward compatibility with existing consumers/tests — they are
// now views over `SPORT_CONFIG`.
import { SPORT_CONFIG } from './useSportConfig'

export const HEURISTIC_ORDER = SPORT_CONFIG.heuristicOrder

export const HEURISTIC_LABELS: Record<string, string> = SPORT_CONFIG.heuristicLabels

export const MODEL_DISPLAY_NAMES: Record<string, string> = SPORT_CONFIG.modelDisplayNames

/**
 * Sort an array of objects by their heuristic field matching
 * HEURISTIC_ORDER (Weighted Tip → Best Bet → YOLO).
 * Items whose heuristic is not in the list sink to the end.
 */
export function sortByHeuristicOrder<T extends { heuristic: string }>(
  items: T[],
): T[] {
  const order = new Map(HEURISTIC_ORDER.map((key, i) => [key, i]))
  return [...items].sort(
    (a, b) =>
      (order.get(a.heuristic) ?? Infinity) -
      (order.get(b.heuristic) ?? Infinity),
  )
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
  //
  // DESIGN-FIX (hydration): formatting without an explicit timeZone
  // made the prerendered HTML (built in UTC) differ from the client's
  // en-AU rendering — a systematic hydration mismatch on every card
  // with a date.  Pin to the sport's display timezone from the config
  // (P4-2): identical text server and client, venue-consistent times.
  const DATE_TZ = SPORT_CONFIG.displayTimezone

  const formatDate = (dateStr: string | null | undefined): string => {
    if (!dateStr) return '—'
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-AU', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: DATE_TZ,
    })
  }

  const formatDateShort = (dateStr: string | null | undefined): string => {
    if (!dateStr) return '—'
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-AU', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      timeZone: DATE_TZ,
    })
  }

  const formatTime = (dateStr: string | null | undefined): string => {
    if (!dateStr) return '—'
    const date = new Date(dateStr)
    return date.toLocaleTimeString('en-AU', {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: DATE_TZ,
    })
  }

  /**
   * DESIGN-FIX (copy): normalises AI explanation copy for display —
   * em-dashes (the most recognisable LLM copy tell) become en-dashes.
   * The DB source text stays untouched.
   */
  const formatExplanation = formatExplanationImpl

  return { formatHeuristic, getModelDisplayName, formatDate, formatDateShort, formatTime, formatExplanation }
}

// Module-level implementation so non-composable contexts (pure computed
// helpers in components) can normalise copy without calling the composable.
export const formatExplanationImpl = (text: string | null | undefined): string => {
  if (!text) return ''
  return text.replace(/—/g, '–')
}
