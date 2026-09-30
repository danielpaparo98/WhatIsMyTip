// LEAGUE-ROUTES (2026-09-30, user request): the non-AFL leagues become
// first-class URLs (/{league}) while AFL stays at '/'.  On the app's
// FIRST route resolution only (initial hydration — never internal
// client-side navigations), a visitor landing on '/' is redirected to
// their persisted non-AFL league so the URL matches the league they
// follow.  Internal navigations to '/' (logo link, league dropdown AFL
// choice, nav "Tips" link) are INTENTIONAL — that is the escape hatch
// back to AFL — so isFirstNavigation is part of the contract; the
// global route middleware owns the first-navigation bookkeeping and the
// client-only guard.  This module is deliberately pure: no Nuxt
// auto-imports, no vue imports, so it is importable from vitest, Nuxt
// middleware, and components alike.

import { LEAGUES } from './useSportConfig'

/**
 * Decide the redirect target for the remembered-league rule.
 *
 * @param storedKey League key persisted in localStorage (`wimt-league`),
 *   or null when nothing is stored.
 * @param isFirstNavigation True only on the app's first route resolution
 *   (initial hydration).
 * @param currentPath The route path being resolved (e.g. '/').
 * @returns '/{league}' to redirect to, or null to stay on currentPath.
 */
export function resolveLeagueRedirect(
  storedKey: string | null,
  isFirstNavigation: boolean,
  currentPath: string,
): string | null {
  // Only the app's initial route resolution may redirect; every later
  // navigation to '/' is a deliberate choice (escape hatch to AFL).
  if (!isFirstNavigation) return null

  // Root only.  Tolerate a bare trailing slash for raw-path inputs;
  // vue-router normally hands us exactly '/'.
  const normalized =
    currentPath.length > 1 && currentPath.endsWith('/')
      ? currentPath.slice(0, -1)
      : currentPath
  if (normalized !== '/' && normalized !== '') return null

  // A known, non-AFL stored league is required.  AFL lives at '/' (a
  // '/' -> '/' redirect would loop), and absent/unknown keys mean "no
  // remembered league" — stay put in both cases.
  if (!storedKey || storedKey === 'afl') return null
  const isKnownLeague = LEAGUES.some((league) => league.key === storedKey)
  if (!isKnownLeague) return null

  return `/${storedKey}`
}
