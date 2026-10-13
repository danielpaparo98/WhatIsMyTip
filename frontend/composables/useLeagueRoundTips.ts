// League round tips (FCL-12 forward tips, 2026-10-13 user request).
//
// The /{league} round pages previously rendered bare status labels
// ("Scheduled") for upcoming fixtures — the state-league era design
// predated first-class league heuristics. FCL-12 gives every league
// forward tips (picks made from prior completed evidence only), exposed
// through GET /api/tips/league-round — a BULK endpoint deliberately
// WITHOUT AI explanations so a round-page view never pays the LLM
// pipeline (the per-event /api/tips/league endpoint owns explanations
// for the match pages).
//
// Fetch pattern = useLeagueEvents' awaited useAsyncData contract:
// reactive per-(league, round) cache key + `dedupe: 'cancel'`, so the
// payload inlines at generate time (roundId derives from the already
// awaited fixtures fetch) and rapid navigation can never paint stale
// tips. A failed/absent tips payload is NON-FATAL by contract — the
// page degrades to the status labels it showed before this existed.

import { computed } from 'vue'
import type { Ref } from 'vue'

/** One fixture's stored picks, keyed by heuristic name. */
export type LeagueRoundPicks = Record<string, string | null>

export interface LeagueRoundTipFixture {
  event_id: number
  slug: string
  round: number | null
  starts_at: string | null
  status: string
  completed: boolean
  home_team: string
  away_team: string
  picks: LeagueRoundPicks
}

export interface LeagueRoundTipsPayload {
  league: string
  round: number
  season: string | null
  fixtures: LeagueRoundTipFixture[]
  count: number
}

export type LeagueRoundTipsSource =
  | string
  | Readonly<Ref<string | null | undefined>>

/**
 * Reactive bulk tips for the league's current round.
 *
 * `tipsBySlug` maps event slug → picks dict so the round cards can look
 * their picks up O(1). A null/failed payload yields an empty map (the
 * cards then keep their pre-FCL status labels — graceful degradation).
 */
export async function useLeagueRoundTips(
  leagueSource: LeagueRoundTipsSource,
  roundId: Readonly<Ref<number | null>>,
) {
  const drivingLeague = computed<string | null>(() =>
    typeof leagueSource === 'string' ? leagueSource : leagueSource.value ?? null,
  )

  const {
    data,
    error: fetchError,
    pending,
    refresh: refetch,
  } = await useAsyncData<LeagueRoundTipsPayload | null>(
    computed(
      () => `league-round-tips-${drivingLeague.value}-${roundId.value}`,
    ),
    async () => {
      const league = drivingLeague.value
      const round = roundId.value
      // 'afl' is canonicalised to '/' by the page; a missing round means
      // the fixtures payload has not landed yet — nothing to fetch.
      if (!league || league === 'afl' || round === null) return null
      const config = useRuntimeConfig()
      const apiBase = config.public.apiBase as string
      return await $fetch<LeagueRoundTipsPayload>(
        `${apiBase}/api/tips/league-round`,
        { params: { league, round } },
      ).catch(() => null)
    },
    { dedupe: 'cancel' },
  )

  // Client-side freshness on hydrated visits (same contract as
  // useLeagueEvents): the prerendered payload paints first, then ONE
  // background refresh pulls tips generated after the build (the daily
  // league-sync cron runs long after any static build).
  if (import.meta.client) {
    onNuxtReady(() => {
      void refetch()
    })
  }

  const tipsBySlug = computed<Record<string, LeagueRoundPicks>>(() => {
    const out: Record<string, LeagueRoundPicks> = {}
    for (const fixture of data.value?.fixtures ?? []) {
      out[fixture.slug] = fixture.picks ?? {}
    }
    return out
  })

  return { tipsBySlug, pending, fetchError }
}
