// LEAGUE-ROUTES (2026-09-30, user request): pure route rules for the
// /{league}/match/{slug} event detail pages.
//
// The rules are split out of the page SFC for the same reason
// useGameSlug.ts exists for /game/[slug]: vitest cannot mount Nuxt
// pages, but the validate decision table and the cross-league
// ownership check are exactly the behaviour worth unit-testing.  The
// page (pages/[league]/match/[slug].vue) maps these verdicts onto
// Nuxt's validate()/fetch contract.
//
// Deliberately dependency-light: imports only pure constants (no Nuxt
// auto-imports, no fetch) so it runs under plain vitest.

import { LEAGUES } from './useSportConfig'
// LEAGUE-ROUTES (2026-09-30, code review): import the canonical home
// (lib/leagueRoutes.ts — Nuxt-free, type-only useApi imports) instead
// of the useLeagueEvents re-export, which transitively dragged the Vue
// runtime into this deliberately dependency-light module. lib imports
// no composables, so there is no import cycle.
import { LEAGUE_COMPETITION_NAMES } from '../lib/leagueRoutes'

/**
 * Event-slug shape, mirroring the backend Path constraint
 * (`max_length=16`) on GET /api/events/{slug}: 1–16 chars of
 * [A-Za-z0-9_-].  Anything else can never exist in the DB, so the
 * route 404s before a pointless API round-trip.
 */
export const EVENT_SLUG_REGEX = /^[A-Za-z0-9_-]{1,16}$/

export function isValidEventSlug(slug: unknown): boolean {
  return typeof slug === 'string' && EVENT_SLUG_REGEX.test(slug)
}

/**
 * Verdict for the match-page route params — the page's validate maps
 * these onto Nuxt outcomes:
 *   'ok'            → render the page
 *   'redirect-home' → navigateTo('/') (see below)
 *   'not-found'     → return false → 404
 */
export type LeagueMatchVerdict = 'ok' | 'redirect-home' | 'not-found'

/**
 * Decide whether /{league}/match/{slug} is a routable URL.
 *
 * Same contract as the league home page's validate (subtask 05): keys
 * must be exact LEAGUES entries (routes are lowercase — no case
 * tolerance, so /WAFL/... 404s rather than silently rendering), and
 * the slug must satisfy the backend Path constraint.
 *
 * 'afl' is the one key that redirects instead of rendering: AFL
 * matches live on the legacy surfaces ('/' home + /game/{slug}), so
 * /afl/match/{slug} bounces to '/' exactly like /afl does.  A valid
 * slug is still required first — malformed URLs 404 even under /afl.
 */
export function validateLeagueMatchRoute(
  league: unknown,
  slug: unknown,
): LeagueMatchVerdict {
  if (!isValidEventSlug(slug)) return 'not-found'
  if (typeof league !== 'string') return 'not-found'
  if (league === 'afl') return 'redirect-home'
  return LEAGUES.some((l) => l.key === league) ? 'ok' : 'not-found'
}

/**
 * Cross-league slug-probing guard.
 *
 * GET /api/events/{slug} resolves events from ANY competition, so a
 * slug from another league's fixtures would happily render under this
 * league's URL.  The payload's `competition` is the competitions.name
 * VALUE (e.g. "West Australian Football League"), matched through the
 * LEAGUE_COMPETITION_NAMES contract — never the league key.  Unknown
 * league keys and missing payloads reject (fail closed) rather than
 * render.
 */
export function eventMatchesLeague(
  event: { competition: string } | null | undefined,
  leagueKey: string,
): boolean {
  const expected = LEAGUE_COMPETITION_NAMES[leagueKey]
  if (!expected || !event) return false
  return event.competition === expected
}
