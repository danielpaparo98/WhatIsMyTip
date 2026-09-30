// League events view state (2026-09-26, user request).
//
// Each non-AFL league renders THAT league's own current round of
// fixtures — independent of the AFL state machine (grand-final week /
// post-season must never blank out another league's content).
//
// LEAGUE-ROUTES (2026-09-30, user request): the league is ROUTE-driven.
// The first-class /{league} pages pass the route param, and the legacy
// no-arg mode (reading the global header selector) was removed along
// with pages/index.vue's league branch in subtask 07 — this composable
// now has exactly one call site: pages/[league]/index.vue. The fetch
// pipeline, the pure helpers and the mid-flight stale-league guards
// are unchanged.
//
// Read side = the ADR 0001 discovery + event APIs (`/api/sports`,
// `/api/events`), consumed through `useApi`. Pure helpers are exported
// so the vitest suite can exercise them without mounting components.
//
// NOTE: this module imports types only from useApi (type-import), so
// the pure helpers stay importable from tests without Nuxt runtime
// (they now live in the Nuxt-free lib/leagueRoutes.ts — see below).

import { computed, ref, watch } from 'vue'
import type { Ref } from 'vue'
import type { SportEvent } from '~/composables/useApi'

// LEAGUE-ROUTES (2026-09-30, user request): the pure helpers used to
// live in this file; they moved to lib/leagueRoutes.ts — a Nuxt-free
// module — so the prerender hook and the sitemap can enumerate
// /{league} + /{league}/match/{slug} at build time (nuxt.config.ts is
// evaluated in plain Node and cannot import composables that pull the
// Vue runtime). They are imported here for this composable's own use
// and re-exported so every existing import path (pages, tests) stays
// stable.
import {
  LEAGUE_COMPETITION_NAMES,
  deriveCurrentRound,
  resolveCompetition,
  sortRoundEvents,
} from '../lib/leagueRoutes'

export {
  LEAGUE_COMPETITION_NAMES,
  deriveCurrentRound,
  resolveCompetition,
  sortRoundEvents,
}
export type { CompetitionResolution } from '../lib/leagueRoutes'

// ---------------------------------------------------------------------------
// Composable
// ---------------------------------------------------------------------------

export interface LeagueEventsState {
  /** The derived current round (null before data arrives / when empty). */
  roundId: number | null
  /** The resolved season label, for the round display. */
  seasonLabel: string | null
  /**
   * The FULL season payload as fetched — the /{league} pages feed it to
   * `deriveSeasonState`/`derivePremier`, which need every event (not
   * just the displayed round) to detect the end of a season.
   */
  seasonEvents: SportEvent[]
  /** The current round's events, ordered for display. */
  roundEvents: SportEvent[]
  pending: boolean
  error: string | null
  /** True when the league has no synced competition/season yet. */
  unavailable: boolean
  refresh: () => Promise<void>
}

/**
 * Where the composable reads its league from: the route param.
 *
 * LEAGUE-ROUTES (2026-09-30, user request): REQUIRED — the /{league}
 * pages pass the route key, either as a plain string or a reactive
 * ref/computed (so a param change refetches without remounting). The
 * ref form may briefly hold null (before a param exists); the guards
 * below treat that as "nothing to fetch". The legacy no-arg mode that
 * read the global header selector was removed with the home page's
 * league branch (subtask 07).
 */
export type LeagueEventsSource =
  | string
  | Readonly<Ref<string | null | undefined>>

/**
 * Reactive league view state.
 *
 * Fetches on league change (the route param — hydration or an in-page
 * navigation between leagues triggers the watch), guards every async
 * step against a rapid league change (a slow stale response can never
 * paint the wrong league — same contract as the page's
 * `dedupe: 'cancel'` fetches), and degrades to `unavailable` when a
 * league has no synced data.
 */
export function useLeagueEvents(leagueSource: LeagueEventsSource) {
  const api = useApi()

  // The league that currently owns the in-flight fetch — always the
  // route-derived source. Normalised to null so the guard contract
  // stays total.
  const drivingLeague = computed<string | null>(() =>
    typeof leagueSource === 'string' ? leagueSource : leagueSource.value ?? null,
  )

  const roundId = ref<number | null>(null)
  const seasonLabel = ref<string | null>(null)
  const seasonEvents = ref<SportEvent[]>([])
  const roundEvents = ref<SportEvent[]>([])
  const pending = ref(false)
  const error = ref<string | null>(null)
  const unavailable = ref(false)

  async function fetchLeagueEvents(): Promise<void> {
    const league = drivingLeague.value
    // `null` = no route param yet (never happens past validate(), but
    // keeps the guards total); 'afl' = the AFL home owns this key
    // (/afl canonicalises to '/', LEAGUE-ROUTES subtask 07).
    if (league === null || league === 'afl') return
    pending.value = true
    error.value = null
    unavailable.value = false
    try {
      const sports = await api.getSports().catch(() => null)
      if (drivingLeague.value !== league) return // superseded mid-flight
      const resolved = resolveCompetition(sports?.sports ?? null, league)
      if (!resolved) {
        unavailable.value = true
        roundId.value = null
        seasonLabel.value = null
        seasonEvents.value = []
        roundEvents.value = []
        return
      }
      const payload = await api.getEvents({
        competition: resolved.competitionId,
        season: resolved.seasonLabel,
      })
      if (drivingLeague.value !== league) return // superseded mid-flight
      const events = payload.events ?? []
      const round = deriveCurrentRound(events)
      roundId.value = round
      seasonLabel.value = resolved.seasonLabel
      seasonEvents.value = events
      roundEvents.value =
        round === null
          ? []
          : sortRoundEvents(events.filter(e => e.round_id === round))
    } catch {
      if (drivingLeague.value !== league) return
      error.value = 'Failed to load fixtures'
    } finally {
      if (drivingLeague.value === league) pending.value = false
    }
  }

  watch(drivingLeague, () => void fetchLeagueEvents(), { immediate: true })

  return {
    roundId,
    seasonLabel,
    seasonEvents,
    roundEvents,
    pending,
    error,
    unavailable,
    refresh: fetchLeagueEvents,
  }
}
