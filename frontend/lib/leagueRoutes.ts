// LEAGUE-ROUTES (2026-09-30, user request): build-time enumeration of the
// first-class league URLs — `/{league}` and `/{league}/match/{slug}` —
// shared by the nuxt.config.ts prerender hook and the prerendered
// sitemap (server/routes/sitemap.xml.ts).
//
// WHY a separate module: nuxt.config.ts is evaluated in plain Node
// BEFORE the Nuxt app exists, so it cannot import composables that pull
// the Vue runtime (useLeagueEvents.ts, useSportConfig.ts). This module
// is deliberately dependency-free — no Vue, no Nuxt auto-imports, and
// only type-imports from useApi (erased at compile time) — so one
// source of truth drives build-time enumeration, sitemap generation,
// the runtime composables, and the vitest suite. `lib/` (not
// `composables/`) also keeps it out of Nuxt's auto-import scan, which
// would collide with useLeagueEvents' re-exports of the same names.
//
// The pure helpers below (LEAGUE_COMPETITION_NAMES, resolveCompetition,
// deriveCurrentRound, sortRoundEvents) are the exact implementations
// that previously lived in useLeagueEvents.ts; useLeagueEvents.ts
// re-exports them so every existing import path stays stable.

import type {
  SportEvent,
  SportsListResponse,
  EventListResponse,
} from '../composables/useApi'

// ---------------------------------------------------------------------------
// League key → competition name (the `competitions.name` value the
// backend sync writes — per packages/shared/ingestion/state_leagues.py
// for the state leagues and national_leagues.py (nrl-expansion-07) for
// the rugby-league competitions).
// Kept explicit rather than guessed from partial names: the mapping is
// a contract with the STATE_LEAGUES + NATIONAL_LEAGUES registries on
// the backend.
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
  // NRL-EXPANSION (nrl-expansion-07): must match NATIONAL_LEAGUES'
  // LeagueConfig.name values exactly (pinned by league-routes.test.ts).
  nrl: 'National Rugby League',
  nrlw: "NRL Women's Premiership",
  origin: 'State of Origin',
}

/**
 * The league keys that own a first-class URL: `/{league}` plus
 * `/{league}/match/{slug}`.
 *
 * Mirrors `useSportConfig.LEAGUES` minus 'afl' — AFL stays at '/' with
 * the legacy /game prerender and is deliberately absent here. A unit
 * test pins this list against the LEAGUES registry so the two cannot
 * drift apart silently.
 */
export const LEAGUE_ROUTE_KEYS: string[] = [
  'wafl',
  'waflw',
  'vfl',
  'vflw',
  'sanfl',
  'aflw',
  'qafl',
  'qaflw',
  'nwfl',
  'sfl',
  // NRL-EXPANSION (nrl-expansion-07): the rugby-league competitions —
  // appended, never reordered.
  'nrl',
  'nrlw',
  'origin',
]

/**
 * LEAGUE-ROUTES (2026-09-30, code review): limit for the FULL-SEASON
 * events fetch — now shared by build-time enumeration AND the runtime
 * composable (useLeagueEvents), so the name is no longer
 * prerender-specific. Must be high enough to pull a FULL season in one
 * request — `deriveCurrentRound` needs the future rounds of the fixture
 * to exist to pick the current one, and `deriveSeasonState`'s
 * every-event-settled check needs the whole season, not its first 100
 * events (the backend default `limit=100` truncated long seasons into
 * a false mid-season "Premiers" celebration).
 */
export const SEASON_EVENT_LIMIT = 500

/**
 * Backward-compat alias for the prerender-era name — external readers
 * (docs, future callers) may still reference it.
 */
export const PRERENDER_EVENT_LIMIT = SEASON_EVENT_LIMIT

/** Result of resolving a league key against the `/api/sports` payload. */
export interface CompetitionResolution {
  competitionId: number
  seasonLabel: string
  /**
   * TEAM-IDENTITY (2026-09-30, user request): the matched competition's
   * own sport id — scopes the club-crest population's
   * `GET /api/teams?sport=` fetch to the league's sport (all current
   * state leagues use 'afl', but the param stays generic).
   */
  sportId: string
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
    // TEAM-IDENTITY (2026-09-30, user request): sport_id surfaced so the
    // awaited league fetch handler can scope the crest population.
    return {
      competitionId: competition.id,
      seasonLabel: season.label,
      sportId: competition.sport_id,
    }
  }
  return null
}

/**
 * AFLW-2026 REGRESSION (2026-11-11, prod): finals bracket placeholder
 * detection. The AFL platform feed emits unresolved finals as fixtures
 * whose SIDES are bracket expressions — "1st" vs "4th", "Loser of QF1"
 * vs "Winner of EF1", "Winner of PF1" vs "Winner of PF2" — at venue
 * "To Be Confirmed", and never closes them out when the real fixtures
 * land. Such events must never pin the displayed round, never render as
 * cards, and never block the season-complete derivation.
 *
 * Deliberately pure + Nuxt-free (shared by the runtime composables, the
 * prerender enumeration, and useSeasonState). An event counts as a
 * placeholder only when it HAS sides and EVERY side is a bracket
 * expression — a real fixture paired against a not-yet-resolved side
 * stays real (we simply don't know enough to call it a placeholder).
 */
export function isPlaceholderEvent(e: SportEvent): boolean {
  const names = e.participants.map((p) => p.participant_name.trim().toLowerCase())
  if (names.length === 0) return false
  return names.every(isBracketSideName)
}

/** One bracket-expression side name ("1st", "Loser of QF1", "TBC"). */
function isBracketSideName(name: string): boolean {
  if (name === 'tbd' || name === 'tbc') return true
  // Ladder positions: 1st/2nd/3rd/4th/…/8th.
  if (/^\d+(st|nd|rd|th)$/.test(name)) return true
  // Bracket outcomes: "Winner of QF1", "Loser of EF1", …
  if (/^(winner|loser) of\b/.test(name)) return true
  return false
}

/**
 * AFLW-2026 REGRESSION (2026-11-11, prod): stale-scheduled detection.
 * A REAL match completes within hours of its start; a fixture still
 * `scheduled` more than STALE_SCHEDULED_HOURS after its start time is
 * feed debris (a placeholder that was synced, or a result the feed
 * failed to close). Such events must not pin `deriveCurrentRound` on a
 * long-past round.
 *
 * The 48h threshold keeps genuine in-progress matches live (their
 * starts_at is minutes-to-hours in the past while the result sync
 * lands).
 */
const STALE_SCHEDULED_MS = 48 * 60 * 60 * 1000

export function isStaleScheduled(e: SportEvent, now: Date): boolean {
  if (e.completed || e.status === 'cancelled' || e.status === 'void') return false
  if (!e.starts_at) return false
  const startMs = Date.parse(e.starts_at)
  if (Number.isNaN(startMs)) return false
  return now.getTime() - startMs > STALE_SCHEDULED_MS
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
 *
 * AFLW-2026 REGRESSION (2026-11-11): bracket-placeholder events (see
 * `isPlaceholderEvent`) and stale-scheduled events (see
 * `isStaleScheduled`) are ignored entirely — a finals bracket that the
 * feed never resolves must not pin the displayed round to the first
 * finals week forever.
 */
export function deriveCurrentRound(
  events: SportEvent[],
  now: Date = new Date(),
): number | null {
  const valid = events.filter(
    (e) => typeof e.round_id === 'number' && !isPlaceholderEvent(e),
  )
  if (valid.length === 0) return null

  const isLive = (e: SportEvent): boolean =>
    !!e.starts_at &&
    !e.completed &&
    e.status !== 'cancelled' &&
    e.status !== 'void' &&
    !isStaleScheduled(e, now)

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
// Route enumeration
// ---------------------------------------------------------------------------

/**
 * Pure route list builder — the single definition of "which league URLs
 * exist" used by BOTH the prerender hook and the sitemap.
 *
 * - `/{league}` AND `/{league}/performance` for every non-AFL key,
 *   ALWAYS (even unsynced leagues — the pages degrade to their
 *   "unavailable"/empty state; a stale 404 in the wild is worse than a
 *   graceful empty page). PERFORMANCE-ROUTES (2026-10-07): the
 *   performance entry is emitted unconditionally next to the home
 *   route; wiring it into prerender/sitemap lands with the
 *   performance-page subtasks, keeping intermediate builds green.
 * - `/{league}/match/{slug}` for each event of the league's derived
 *   current round, only when its season payload was fetched.
 * - 'afl' is filtered out defensively even if a caller passes it —
 *   AFL lives at '/' and its matches on /game/{slug}.
 *
 * `now` is injectable so tests (and a future caller) can pin the
 * round derivation; production enumeration passes the build clock.
 */
export function buildLeagueRoutes(
  leagueKeys: string[],
  eventsByLeague: Record<string, SportEvent[]>,
  now: Date = new Date(),
): string[] {
  const routes: string[] = []
  for (const league of leagueKeys) {
    if (league === 'afl') continue
    routes.push(`/${league}`)
    // PERFORMANCE-ROUTES (2026-10-07): unconditional, same graceful
    // contract as the home route — unsynced leagues still ship their
    // performance page (it renders its empty state).
    routes.push(`/${league}/performance`)
    const events = eventsByLeague[league]
    if (!events || events.length === 0) continue
    const round = deriveCurrentRound(events, now)
    if (round === null) continue
    // AFLW-2026 REGRESSION (2026-11-11): bracket placeholders share the
    // real fixtures' round — never enumerate prerendered match pages for
    // "1st vs 4th" style rows.
    const roundEvents = sortRoundEvents(
      events.filter((e) => e.round_id === round && !isPlaceholderEvent(e)),
    )
    for (const event of roundEvents) {
      if (typeof event.slug === 'string' && event.slug.length > 0) {
        routes.push(`/${league}/match/${event.slug}`)
      }
    }
  }
  return routes
}

/**
 * Where enumeration failures are reported. `source` is either a league
 * key or 'sports' (the discovery call). Callers wire this to
 * `nitro.logger.warn` — prerender must NEVER fail because of leagues,
 * mirroring the AFL enumeration's try/catch + warn contract.
 */
export type LeagueEnumerationErrorSink = (source: string, err: unknown) => void

/**
 * Fetch-driven enumeration: `/api/sports` discovery, then one
 * full-season events fetch per synced competition, then
 * `buildLeagueRoutes` over whatever succeeded.
 *
 * Degradation contract (NEVER throws):
 * - discovery failure → home + performance routes only,
 *   `onError('sports', …)`
 * - one league's events fetch failing → that league keeps its home
 *   and performance routes and loses only its match routes,
 *   `onError(league, …)`
 * - unsynced leagues (no competition/season) → home + performance
 *   routes only, and that is an EXPECTED state, not an error —
 *   nothing is reported.
 */
export async function enumerateLeagueRoutes(
  apiBase: string,
  onError?: LeagueEnumerationErrorSink,
  /**
   * AFLW-2026 REGRESSION (2026-11-11): injectable build clock, the same
   * contract as `buildLeagueRoutes` — staleness-aware round derivation
   * needs a pinned `now` in tests (default = the wall clock).
   */
  now: Date = new Date(),
): Promise<string[]> {
  const eventsByLeague: Record<string, SportEvent[]> = {}
  try {
    // LEAGUE-ROUTES (2026-09-30, code review): every build-time fetch is
    // bounded by an abort timeout — a hung backend must stall `nuxt
    // generate` for at most 15s, then fall through to this module's
    // graceful-degradation contract (Node 18+ ships AbortSignal.timeout).
    const sportsRes = await fetch(`${apiBase}/api/sports`, {
      signal: AbortSignal.timeout(15000),
    })
    if (!sportsRes.ok) throw new Error(`sports HTTP ${sportsRes.status}`)
    const sports = (await sportsRes.json()) as SportsListResponse

    await Promise.all(
      LEAGUE_ROUTE_KEYS.map(async (league) => {
        try {
          const resolved = resolveCompetition(sports?.sports ?? null, league)
          if (!resolved) return // unsynced → home + performance routes only
          const eventsRes = await fetch(
            `${apiBase}/api/events?competition=${resolved.competitionId}` +
            `&season=${encodeURIComponent(resolved.seasonLabel)}` +
            `&limit=${SEASON_EVENT_LIMIT}`,
            { signal: AbortSignal.timeout(15000) },
          )
          if (!eventsRes.ok) throw new Error(`events HTTP ${eventsRes.status}`)
          const payload = (await eventsRes.json()) as EventListResponse
          eventsByLeague[league] = payload?.events ?? []
        } catch (err) {
          // Per-league degradation: the league keeps its home and
          // performance routes and loses only this build's match routes.
          onError?.(league, err)
        }
      }),
    )
  } catch (err) {
    // Discovery failed entirely — homes + performance routes only;
    // match routes arrive with the next successful build.
    onError?.('sports', err)
  }
  return buildLeagueRoutes(LEAGUE_ROUTE_KEYS, eventsByLeague, now)
}
