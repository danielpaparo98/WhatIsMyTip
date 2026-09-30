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
// now has exactly one call site: pages/[league]/index.vue.
//
// LEAGUE-ROUTES (2026-09-30, code review): the fetch moved into an
// AWAITED `useAsyncData` (same shape as pages/index.vue's SEO-C1 fix
// and pages/[league]/match/[slug].vue). The previous watch+refs
// pipeline only ran on the client, so prerender baked hero+spinner
// with zero fixtures content; the awaited fetch now runs at generate
// time and inlines the payload (hydration needs no refetch — the exact
// rationale documented on the AFL home). Staleness protection moves
// from the hand-rolled mid-flight guards to the reactive per-league
// cache key + `dedupe: 'cancel'` (the game/match pages' H-5 contract),
// and a client-side refresh after Nuxt-ready preserves the old
// immediate-watch freshness on hydrated visits.
//
// Read side = the ADR 0001 discovery + event APIs (`/api/sports`,
// `/api/events`), consumed through `useApi`. Pure helpers are exported
// so the vitest suite can exercise them without mounting components.
//
// NOTE: this module imports types only from useApi (type-import), so
// the pure helpers stay importable from tests without Nuxt runtime
// (they now live in the Nuxt-free lib/leagueRoutes.ts — see below).

import { computed } from 'vue'
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
  SEASON_EVENT_LIMIT,
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
 * ref form may briefly hold null (before a param exists); the fetch
 * handler below treats that as "nothing to fetch". The legacy no-arg
 * mode that read the global header selector was removed with the home
 * page's league branch (subtask 07).
 */
export type LeagueEventsSource =
  | string
  | Readonly<Ref<string | null | undefined>>

/**
 * What the useAsyncData handler resolves to: the raw fetch outcome,
 * before the view derivations (round, round events, season state) are
 * computed from it. `unavailable` distinguishes "competition not
 * synced yet" (an expected, gracefully-degraded page state) from a
 * genuine fetch error (surfaced as `error`).
 */
interface LeagueSeasonPayload {
  seasonLabel: string | null
  events: SportEvent[]
  unavailable: boolean
}

/**
 * Reactive league view state.
 *
 * LEAGUE-ROUTES (2026-09-30, code review): awaited `useAsyncData` with
 * a REACTIVE per-league cache key (`league-events-{league}`) — a key
 * change (route param change) triggers the fetch automatically and
 * every league gets its own payload-cached slot (the game/match pages'
 * H-5 contract); `dedupe: 'cancel'` drops superseded in-flight fetches
 * on rapid navigation, replacing the old hand-rolled mid-flight guards
 * (the handler reads the same driving league the key was derived from,
 * so a stale response can never paint the wrong league).
 *
 * The season fetch asks for the FULL season (SEASON_EVENT_LIMIT) —
 * the backend's default `limit=100` would truncate a long season into
 * a false mid-season "Premiers" celebration.
 */
export async function useLeagueEvents(leagueSource: LeagueEventsSource) {
  const api = useApi()

  // The league that currently owns the fetch — always the route-derived
  // source. Normalised to null so the handler's guards stay total.
  const drivingLeague = computed<string | null>(() =>
    typeof leagueSource === 'string' ? leagueSource : leagueSource.value ?? null,
  )

  const fetchLeagueEvents = async (): Promise<LeagueSeasonPayload> => {
    const league = drivingLeague.value
    // `null` = no route param yet (never happens past validate(), but
    // keeps the guards total); 'afl' = the AFL home owns this key
    // (/afl canonicalises to '/', LEAGUE-ROUTES subtask 07).
    if (league === null || league === 'afl') {
      return { seasonLabel: null, events: [], unavailable: false }
    }
    const sports = await api.getSports().catch(() => null)
    const resolved = resolveCompetition(sports?.sports ?? null, league)
    if (!resolved) {
      return { seasonLabel: null, events: [], unavailable: true }
    }
    // LEAGUE-ROUTES (2026-09-30, code review): the FULL-season limit is
    // load-bearing — without it the backend's default (first 100 events
    // of the season, starts_at ASC) makes `deriveCurrentRound` pin a
    // stale round and `deriveSeasonState` see a fully-settled payload
    // MID-SEASON → false "Premiers" on every league home.
    const payload = await api.getEvents({
      competition: resolved.competitionId,
      season: resolved.seasonLabel,
      limit: SEASON_EVENT_LIMIT,
    })
    return {
      seasonLabel: resolved.seasonLabel,
      events: payload.events ?? [],
      unavailable: false,
    }
  }

  const {
    data,
    pending,
    error: fetchError,
    refresh: refetch,
  } = await useAsyncData<LeagueSeasonPayload>(
    computed(() => `league-events-${drivingLeague.value}`),
    fetchLeagueEvents,
    { dedupe: 'cancel' },
  )

  // Client-side freshness on hydrated visits: the inlined payload renders
  // the first paint, then ONE background refresh updates fixtures/results
  // — preserving what the old immediate-watch fetch did on mount, without
  // a refetch during prerender/SSR.
  if (import.meta.client) {
    onNuxtReady(() => {
      void refetch()
    })
  }

  // View state derived from the payload — computed so the awaited
  // fetch result and any later refresh flow through reactively, with
  // the same public shape as the previous watch+refs implementation.
  const seasonEvents = computed<SportEvent[]>(() => data.value?.events ?? [])
  const seasonLabel = computed<string | null>(() => data.value?.seasonLabel ?? null)
  const roundId = computed<number | null>(() => deriveCurrentRound(seasonEvents.value))
  const roundEvents = computed<SportEvent[]>(() =>
    roundId.value === null
      ? []
      : sortRoundEvents(
          seasonEvents.value.filter((e) => e.round_id === roundId.value),
        ),
  )
  const unavailable = computed<boolean>(() => data.value?.unavailable ?? false)
  const error = computed<string | null>(() =>
    fetchError.value ? 'Failed to load fixtures' : null,
  )

  return {
    roundId,
    seasonLabel,
    seasonEvents,
    roundEvents,
    pending,
    error,
    unavailable,
    refresh: refetch,
  }
}
