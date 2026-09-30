// LEAGUE-ROUTES (2026-09-30, user request): remember-the-league
// redirect.  A visitor HARD-loading '/' is bounced to their persisted
// non-AFL league (/{league}) so the URL matches the league they follow.
// The decision logic is pure and lives in
// composables/useLeagueRedirect.ts (resolveLeagueRedirect — unit-tested
// in tests/unit/league-redirect.test.ts); THIS file only wires it into
// Nuxt, with three properties the pure helper cannot own:
//
//   1. Client-only (import.meta.client): the static prerender of '/'
//      must stay the AFL page for every visitor — a server/prerender
//      redirect would bake one league's bounce into everyone's HTML,
//      and localStorage only exists in the browser anyway.
//
//   2. First navigation only: the useState('wimt-has-navigated') flag
//      is flipped after the first run, so every LATER navigation to '/'
//      (logo link, league dropdown AFL choice, nav "Tips" link) is
//      intentional and never redirected — the "go back to AFL" escape
//      hatch.
//
//   3. replace: true — the redirect is a URL correction, not a page the
//      user visited; the back button must return to the real entry
//      URL, not loop through '/'.
//
// NOTE: setActiveLeague on /{league} visits is deliberately NOT done
// here — pages/[league]/index.vue owns that sync (it runs for direct
// hard-loads of /{league} too, where this middleware's stored key is
// irrelevant).  Duplicating it would stomp the dropdown with the
// stored key before the route's own sync.
import { resolveLeagueRedirect } from '~/composables/useLeagueRedirect'
import { readStoredLeagueKey } from '~/composables/useSportConfig'

export default defineNuxtRouteMiddleware((to) => {
  // Prerender/SSR never redirect (property 1 above).
  if (!import.meta.client) return

  const hasNavigated = useState('wimt-has-navigated', () => false)
  const isFirstNavigation = !hasNavigated.value

  // Global middleware runs before any component mounts, so
  // useActiveLeague()'s onMounted hydration has not happened yet —
  // read the persisted key straight from storage (raw; unvalidated).
  const storedKey = readStoredLeagueKey()
  const target = resolveLeagueRedirect(storedKey, isFirstNavigation, to.path)

  // Flip AFTER the decision so the first run is always treated as the
  // initial hydration, whatever it decided.
  hasNavigated.value = true

  if (target) {
    return navigateTo(target, { replace: true })
  }
})
