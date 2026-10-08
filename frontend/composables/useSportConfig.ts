// Single source of truth for sport-specific presentation (P4-2, ADR 0001).
//
// Every piece of copy, ordering, and labelling that assumes a sport
// lives HERE instead of being scattered across components.  Adding a
// sport means adding a SportConfig entry and switching on it — not
// grepping for hardcoded strings.
//
// NOTE: this module stays free of Nuxt/server dependencies (pure
// constants plus a Vue-ref-backed league store) so it is importable
// from components, composables, and tests alike.

import { computed, getCurrentInstance, onMounted, ref } from 'vue'

export interface SportConfig {
  sportId: string
  displayName: string
  /** Compact label for the league selector and other tight UI spots. */
  shortLabel: string
  /** Word for one contest: 'Game' (AFL) vs 'Match' (tennis). */
  contestNoun: string
  /** Word for a scheduled stage: 'Round' (AFL) vs 'Tournament'. */
  stageNoun: string
  /** Display timezone — pinned so prerendered HTML matches the client. */
  displayTimezone: string
  /** Heuristic display order (Boosted Tip first — BT-1 flagship). */
  heuristicOrder: string[]
  heuristicLabels: Record<string, string>
  modelDisplayNames: Record<string, string>
}

/** The AFL bootstrap config — the current behaviour, made explicit. */
export const AFL_CONFIG: SportConfig = {
  sportId: 'afl',
  displayName: 'AFL',
  shortLabel: 'AFL',
  contestNoun: 'Game',
  stageNoun: 'Round',
  displayTimezone: 'Australia/Sydney',
  // BT-1 (boosted-tip): boosted_tip leads as the flagship heuristic;
  // best_bet is no longer generated (removed from the orchestrator) but
  // stays listed so historical tips/backtest rows still render.
  heuristicOrder: ['boosted_tip', 'weighted_tip', 'best_bet', 'yolo'],
  heuristicLabels: {
    boosted_tip: 'Boosted Tip',
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

export interface LeagueMeta {
  key: string
  displayName: string
  shortLabel: string
}

// NOTE: the league list is STATIC for now — dynamic discovery via
// /api/sports arrives with the read-side cutover (P4-1 follow-up).
// AFL first (current product), then the state leagues.
export const LEAGUES: LeagueMeta[] = [
  { key: 'afl', displayName: 'AFL', shortLabel: 'AFL' },
  { key: 'wafl', displayName: 'WAFL', shortLabel: 'WAFL' },
  { key: 'waflw', displayName: 'WAFLW', shortLabel: 'WAFLW' },
  { key: 'vfl', displayName: 'VFL', shortLabel: 'VFL' },
  { key: 'vflw', displayName: 'VFLW', shortLabel: 'VFLW' },
  { key: 'sanfl', displayName: 'SANFL', shortLabel: 'SANFL' },
  { key: 'aflw', displayName: 'AFLW', shortLabel: 'AFLW' },
  { key: 'qafl', displayName: 'QAFL', shortLabel: 'QAFL' },
  { key: 'qaflw', displayName: 'QAFLW', shortLabel: 'QAFLW' },
  { key: 'nwfl', displayName: 'NWFL', shortLabel: 'NWFL' },
  { key: 'sfl', displayName: 'SFL', shortLabel: 'SFL' },
]

/** Config cache so every league resolves to a stable object identity. */
const LEAGUE_CONFIGS = new Map<string, SportConfig>([['afl', AFL_CONFIG]])

/** Resolve the SportConfig for a league key (falls back to AFL). */
export function getLeagueConfig(key: string): SportConfig {
  const cached = LEAGUE_CONFIGS.get(key)
  if (cached) return cached
  const meta = LEAGUES.find((l) => l.key === key)
  if (!meta) return AFL_CONFIG
  // State-league configs reuse the AFL styling defaults for now —
  // per-league labels/nouns are refined with the read-side cutover.
  const config: SportConfig = {
    ...AFL_CONFIG,
    sportId: meta.key,
    displayName: meta.displayName,
    shortLabel: meta.shortLabel,
  }
  LEAGUE_CONFIGS.set(key, config)
  return config
}

/** The active sport config — AFL by default.  The live selection is
 *  managed by `useActiveLeague()` below and read via `useSportConfig()`;
 *  consumers that need the CURRENT league must call the composable
 *  rather than import this constant. */
export const SPORT_CONFIG: SportConfig = AFL_CONFIG

export function useSportConfig(): SportConfig {
  return useActiveLeague().activeConfig.value
}

// ---------------------------------------------------------------------------
// Active league
// ---------------------------------------------------------------------------

/** localStorage key for the persisted league selection. */
export const LEAGUE_STORAGE_KEY = 'wimt-league'

export const DEFAULT_LEAGUE_KEY = 'afl'

const activeLeagueKey = ref<string>(DEFAULT_LEAGUE_KEY)

// LEAGUE-ROUTES (2026-09-30, user request): exported for the global
// redirect middleware (middleware/league-redirect.global.ts), which
// runs BEFORE any component's onMounted — so useActiveLeague() has not
// hydrated from storage yet and the middleware must read the persisted
// key itself.  Raw and unvalidated by design: validation (known league,
// non-AFL, root path) is resolveLeagueRedirect's job, keeping this a
// plain "what is in localStorage" accessor.
export function readStoredLeagueKey(): string | null {
  if (typeof window === 'undefined' || !('localStorage' in window)) return null
  try {
    return window.localStorage.getItem(LEAGUE_STORAGE_KEY)
  } catch {
    return null
  }
}

function hydrateFromStorage() {
  // Storage is authoritative: an absent or unknown key resets the
  // selection to the default instead of keeping a stale in-memory value.
  const stored = readStoredLeagueKey()
  activeLeagueKey.value =
    stored && LEAGUES.some((l) => l.key === stored) ? stored : DEFAULT_LEAGUE_KEY
}

export function useActiveLeague() {
  // SSR/prerender-safe: resolve the stored selection only on the client,
  // after hydration (same pattern as useColorMode).  Outside a component
  // (unit tests, plain scripts) hydrate immediately.
  if (getCurrentInstance()) {
    onMounted(hydrateFromStorage)
  } else {
    hydrateFromStorage()
  }

  const activeLeague = computed(() => activeLeagueKey.value)
  const activeConfig = computed(() => getLeagueConfig(activeLeagueKey.value))

  // LEAGUE-NAV (2026-09-30, user request): the nav's home/Tips links
  // stay inside the active league — AFL resolves to the root, every
  // other league to its own home (/wafl etc.). The league dropdown
  // remains the explicit escape hatch back to AFL.
  const homePath = computed(() =>
    activeLeagueKey.value === DEFAULT_LEAGUE_KEY ? '/' : `/${activeLeagueKey.value}`,
  )

  // PERFORMANCE-ROUTES (2026-10-07): the nav's Performance link stays
  // inside the active league too — AFL owns the root-level
  // /performance page (mirroring '/' = AFL home), every other league
  // its own /{league}/performance page.
  const performancePath = computed(() =>
    activeLeagueKey.value === DEFAULT_LEAGUE_KEY
      ? '/performance'
      : `/${activeLeagueKey.value}/performance`,
  )

  function setActiveLeague(key: string): void {
    if (!LEAGUES.some((l) => l.key === key)) return
    activeLeagueKey.value = key
    if (typeof window !== 'undefined' && 'localStorage' in window) {
      try {
        window.localStorage.setItem(LEAGUE_STORAGE_KEY, key)
      } catch {
        // Storage unavailable (private mode, quota) — keep in-memory only.
      }
    }
  }

  return { activeLeague, activeConfig, homePath, performancePath, setActiveLeague }
}
