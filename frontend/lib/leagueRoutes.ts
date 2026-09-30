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
]

/**
 * Limit for the season events fetch during enumeration. Must be high
 * enough to pull a FULL season in one request — `deriveCurrentRound`
 * needs the future rounds of the fixture to exist to pick the current
 * one (a default-limited fetch would mistake the last fetched round
 * for the season's end).
 */
export const PRERENDER_EVENT_LIMIT = 500

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
// Route enumeration
// ---------------------------------------------------------------------------

/**
 * Pure route list builder — the single definition of "which league URLs
 * exist" used by BOTH the prerender hook and the sitemap.
 *
 * - `/{league}` for every non-AFL key, ALWAYS (even unsynced leagues —
 *   the pages degrade to their "unavailable" state; a stale 404 in the
 *   wild is worse than a graceful empty page).
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
    const events = eventsByLeague[league]
    if (!events || events.length === 0) continue
    const round = deriveCurrentRound(events, now)
    if (round === null) continue
    const roundEvents = sortRoundEvents(
      events.filter(e => e.round_id === round),
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
 * - discovery failure → home routes only, `onError('sports', …)`
 * - one league's events fetch failing → that league keeps its home
 *   route and loses only its match routes, `onError(league, …)`
 * - unsynced leagues (no competition/season) → home route only, and
 *   that is an EXPECTED state, not an error — nothing is reported.
 */
export async function enumerateLeagueRoutes(
  apiBase: string,
  onError?: LeagueEnumerationErrorSink,
): Promise<string[]> {
  const eventsByLeague: Record<string, SportEvent[]> = {}
  try {
    const sportsRes = await fetch(`${apiBase}/api/sports`)
    if (!sportsRes.ok) throw new Error(`sports HTTP ${sportsRes.status}`)
    const sports = (await sportsRes.json()) as SportsListResponse

    await Promise.all(
      LEAGUE_ROUTE_KEYS.map(async (league) => {
        try {
          const resolved = resolveCompetition(sports?.sports ?? null, league)
          if (!resolved) return // unsynced → home route only
          const eventsRes = await fetch(
            `${apiBase}/api/events?competition=${resolved.competitionId}` +
            `&season=${encodeURIComponent(resolved.seasonLabel)}` +
            `&limit=${PRERENDER_EVENT_LIMIT}`,
          )
          if (!eventsRes.ok) throw new Error(`events HTTP ${eventsRes.status}`)
          const payload = (await eventsRes.json()) as EventListResponse
          eventsByLeague[league] = payload?.events ?? []
        } catch (err) {
          // Per-league degradation: the league keeps its home route and
          // loses only this build's match routes.
          onError?.(league, err)
        }
      }),
    )
  } catch (err) {
    // Discovery failed entirely — homes only; match routes arrive with
    // the next successful build.
    onError?.('sports', err)
  }
  return buildLeagueRoutes(LEAGUE_ROUTE_KEYS, eventsByLeague)
}
