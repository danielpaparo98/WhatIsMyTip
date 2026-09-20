import type { LatestRoundResponse } from '~/composables/useApi'

/**
 * Shared, app-wide latest-round state (FIX M-4, 2026-09 review).
 *
 * Previously three call sites each fetched `/api/games?latest=true`
 * independently (index page, OffSeasonBanner, ConfettiEffect) and two
 * of them also ran their own 5-minute pollers — 3x the backend load
 * for the same data.
 *
 * Now the index page is the single IO owner:
 *  1. It fetches the round via `useAsyncData` (runs at generate time,
 *     data is inlined into the prerendered HTML + payload).
 *  2. It publishes the value here via `setLatestRound()` whenever it
 *     changes (initial SSR value and each poll tick).
 *  3. Other consumers (OffSeasonBanner, ConfettiEffect) call
 *     `useLatestRound()` and just READ `latestRound` — no fetches, no
 *     pollers of their own.
 *
 * The store is seeded from the Nuxt payload when available, so passive
 * consumers render immediately on hydration without an extra request.
 */
export const useLatestRound = () => {
  const nuxtApp = useNuxtApp()

  const latestRound = useState<LatestRoundResponse | null>('shared-latest-round', () => {
    // Seed from the index page's useAsyncData payload (key:
    // 'latest-round') so passive consumers hydrate without fetching.
    const seeded = nuxtApp.payload?.data?.['latest-round'] as
      | LatestRoundResponse
      | undefined
    return seeded ?? null
  })

  const setLatestRound = (value: LatestRoundResponse | null | undefined) => {
    if (value) latestRound.value = value
  }

  return { latestRound, setLatestRound }
}

/** Poll interval shared by the index page's auto-refresh. */
export const AUTO_REFRESH_MS = 5 * 60 * 1000

// ---------------------------------------------------------------------------
// Home-page state resolution (grand-final uplift, 2026-09).
//
// The home page renders one of three mutually-exclusive states, driven
// entirely by the latest-round locator.  Post-season wins over grand
// final: once every GF-round game is completed the backend flips
// `is_post_season` on and the celebration page takes over — so the
// "grand final week" state requires `is_grand_final && !is_post_season`.
// Kept as a pure exported function so the gating logic is unit-testable
// without mounting the page.
// ---------------------------------------------------------------------------
export type HomeState = 'grand_final' | 'post_season' | 'regular'

export function resolveHomeState(
  round: Pick<LatestRoundResponse, 'is_grand_final' | 'is_post_season'> | null | undefined,
): HomeState {
  if (round?.is_post_season) return 'post_season'
  if (round?.is_grand_final) return 'grand_final'
  return 'regular'
}
