// League-aware home view (2026-09-26, user request).
//
// Selecting a state league (WAFL, VFL, …) in the header swaps the home
// page to THAT league's own current round of fixtures — independent of
// the AFL state machine (grand-final week / post-season must never
// blank out another league's content).
//
// Read side = the ADR 0001 discovery + event APIs (`/api/sports`,
// `/api/events`), consumed through `useApi`. Pure helpers are exported
// so the vitest suite can exercise them without mounting components.
//
// NOTE: this module imports types only from useApi (type-import), so
// the pure helpers stay importable from tests without Nuxt runtime.

import { computed, ref, watch } from 'vue'
import type { SportEvent, SportsListResponse } from '~/composables/useApi'

// ---------------------------------------------------------------------------
// League key → competition name (the `competitions.name` value the
// backend sync writes, per packages/shared/ingestion/state_leagues.py).
// Kept explicit rather than guessed from partial names: the mapping is
// a contract with the STATE_LEAGUES registry on the backend.
// ---------------------------------------------------------------------------

export const LEAGUE_COMPETITION_NAMES: Record<string, string> = {
  wafl: 'West Australian Football League',
  waflw: "Western Australian Women's Football League",
  sanfl: 'South Australian National Football League',
  vfl: 'Victorian Football League',
  vflw: "Victorian Women's Football League",
  aflw: "AFL Women's",
  qafl: 'Queensland Australian Football League',
  qaflw: "Queensland Australian Football League Women's",
  nwfl: 'North West Football League',
  sfl: 'Southern Football League',
}

/** Result of resolving a league key against the `/api/sports` payload. */
export interface CompetitionResolution {
  competitionId: number
  seasonLabel: string
}

/**
 * Map a league key to its competition id + season label.
 *
 * Season selection: the season marked `is_current` wins; when none is
 * (the production seed does not mark any), the LATEST label wins —
 * labels sort lexicographically for four-digit years.
 * Returns null for the AFL key (the legacy view owns it), unknown
 * keys, and competitions that have not been synced yet.
 */
export function resolveCompetition(
  sports: SportsListResponse['sports'] | null | undefined,
  leagueKey: string,
): CompetitionResolution | null {
  const wantedName = LEAGUE_COMPETITION_NAMES[leagueKey]
  if (!wantedName || !sports) return null
  for (const sport of sports) {
    const competition = sport.competitions.find(c => c.name === wantedName)
    if (!competition || competition.seasons.length === 0) continue
    const current = competition.seasons.find(s => s.is_current)
    const latest = [...competition.seasons].sort((a, b) =>
      b.label.localeCompare(a.label),
    )[0]
    const season = current ?? latest
    if (!season) continue
    return { competitionId: competition.id, seasonLabel: season.label }
  }
  return null
}

/**
 * Derive the league's "current" round from a full season of events.
 *
 * Mirrors the AFL locator's semantics: during the season the first
 * round that still has live (dated, not completed/cancelled/void)
 * events wins; once nothing is live the latest round WITH RESULTS
 * wins — so a finished (or partially cancelled) league shows its
 * grand final / last played round, never a blank or cancelled round.
 * Events without a round number (tournament-style) are ignored.
 */
export function deriveCurrentRound(
  events: SportEvent[],
  now: Date = new Date(),
): number | null {
  const valid = events.filter(e => typeof e.round_id === 'number')
  if (valid.length === 0) return null

  const isLive = (e: SportEvent): boolean =>
    !!e.starts_at &&
    !e.completed &&
    e.status !== 'cancelled' &&
    e.status !== 'void'

  const upcoming = valid.filter(isLive)
  if (upcoming.length > 0) {
    return Math.min(...upcoming.map(e => e.round_id as number))
  }

  // Latest round with actual results (completed), then latest-dated,
  // then the highest round number present.
  const datedCompleted = valid.filter(e => e.completed && !!e.starts_at)
  if (datedCompleted.length > 0) {
    const latest = datedCompleted.reduce((acc, e) =>
      Date.parse(e.starts_at as string) > Date.parse(acc.starts_at as string) ? e : acc,
    )
    return latest.round_id
  }
  const dated = valid.filter(e => !!e.starts_at)
  if (dated.length > 0) {
    const latest = dated.reduce((acc, e) =>
      Date.parse(e.starts_at as string) > Date.parse(acc.starts_at as string) ? e : acc,
    )
    return latest.round_id
  }
  return Math.max(...valid.map(e => e.round_id as number))
}

/**
 * Order a round's events by start time (undated last), id as the
 * tiebreak.  Returns a new array — never mutates the source.
 */
export function sortRoundEvents(events: SportEvent[]): SportEvent[] {
  return [...events].sort((a, b) => {
    const ta = a.starts_at ? Date.parse(a.starts_at) : Number.POSITIVE_INFINITY
    const tb = b.starts_at ? Date.parse(b.starts_at) : Number.POSITIVE_INFINITY
    return ta - tb || a.id - b.id
  })
}

// ---------------------------------------------------------------------------
// Composable
// ---------------------------------------------------------------------------

export interface LeagueEventsState {
  /** The derived current round (null before data arrives / when empty). */
  roundId: number | null
  /** The resolved season label, for the round display. */
  seasonLabel: string | null
  /** The current round's events, ordered for display. */
  roundEvents: SportEvent[]
  pending: boolean
  error: string | null
  /** True when the league has no synced competition/season yet. */
  unavailable: boolean
  refresh: () => Promise<void>
}

/**
 * Reactive league view state for the home page.
 *
 * Fetches on league change (the selector's localStorage hydration on
 * mount triggers the watch), guards every async step against a rapid
 * league switch (a slow stale response can never paint the wrong
 * league — same contract as the page's `dedupe: 'cancel'` fetches),
 * and degrades to `unavailable` when a league has no synced data.
 */
export function useLeagueEvents() {
  const api = useApi()
  const { activeLeague } = useActiveLeague()

  const roundId = ref<number | null>(null)
  const seasonLabel = ref<string | null>(null)
  const roundEvents = ref<SportEvent[]>([])
  const pending = ref(false)
  const error = ref<string | null>(null)
  const unavailable = ref(false)

  async function fetchLeagueEvents(): Promise<void> {
    const league = activeLeague.value
    if (league === 'afl') return // the legacy AFL view owns this key
    pending.value = true
    error.value = null
    unavailable.value = false
    try {
      const sports = await api.getSports().catch(() => null)
      if (activeLeague.value !== league) return // superseded mid-flight
      const resolved = resolveCompetition(sports?.sports ?? null, league)
      if (!resolved) {
        unavailable.value = true
        roundId.value = null
        seasonLabel.value = null
        roundEvents.value = []
        return
      }
      const payload = await api.getEvents({
        competition: resolved.competitionId,
        season: resolved.seasonLabel,
      })
      if (activeLeague.value !== league) return // superseded mid-flight
      const round = deriveCurrentRound(payload.events ?? [])
      roundId.value = round
      seasonLabel.value = resolved.seasonLabel
      roundEvents.value =
        round === null
          ? []
          : sortRoundEvents(
              (payload.events ?? []).filter(e => e.round_id === round),
            )
    } catch {
      if (activeLeague.value !== league) return
      error.value = 'Failed to load fixtures'
    } finally {
      if (activeLeague.value === league) pending.value = false
    }
  }

  watch(activeLeague, () => void fetchLeagueEvents(), { immediate: true })

  return {
    activeLeague,
    roundId,
    seasonLabel,
    roundEvents,
    pending,
    error,
    unavailable,
    refresh: fetchLeagueEvents,
  }
}
