// TypeScript interfaces mirroring the backend Pydantic schemas in
// `backend/packages/shared/schemas/`.  Field-for-field alignment is
// important so the frontend can safely render null values without
// crashing (e.g. TBD fixtures with no home_team).  See CR-006 from
// Phase 2b.  When the Pydantic schema changes, this file must change
// to match — keep them in sync.

/** Mirrors `GameResponse` in backend/.../schemas/games.py. */
export interface Game {
  id: number
  slug: string
  /** @deprecated provider-specific id — see `source` (P3-2, ADR 0001) */
  squiggle_id: number | null
  /** Which feed provider produced this fixture ("squiggle" today). */
  source: string
  round_id: number
  season: number
  // home_team / away_team / venue are nullable in Postgres to support
  // stub future-fixture rows from the Squiggle feed.
  home_team: string | null
  away_team: string | null
  home_score: number | null
  away_score: number | null
  venue: string | null
  date: string | null
  completed: boolean
}

/** Mirrors `TipResponse` in backend/.../schemas/tips.py. */
export interface Tip {
  id: number
  game_id: number
  heuristic: string
  selected_team: string
  margin: number
  confidence: number
  explanation: string
  created_at: string
}

/** Mirrors `ModelPrediction` in backend/.../schemas/games.py. */
export interface ModelPrediction {
  model_name: string
  winner: string
  confidence: number
  margin: number
}

/** Mirrors `MatchAnalysisResponse` in backend/.../schemas/match_analysis.py. */
export interface MatchAnalysis {
  id: number
  game_id: number
  analysis_text: string
  created_at: string
}

/** Mirrors `WeatherResponse` in backend/.../schemas/games.py. */
export interface Weather {
  temperature: number | null
  precipitation: number | null
  wind_speed: number | null
  wind_gusts: number | null
  wind_direction: number | null
  humidity: number | null
  weather_code: number | null
  data_type: string | null
}

/** Mirrors `GameDetailResponse` in backend/.../schemas/games.py. */
export interface GameDetailResponse {
  game: Game
  tips: Tip[]
  model_predictions: ModelPrediction[]
  match_analysis: MatchAnalysis | null
  weather: Weather | null
}

/**
 * Mirrors the inline shape returned by `/api/tips/games-with-tips`
 * (a `GameResponse` flattened with its best-bet `tip` and any
 * `model_predictions`).  See backend/.../api/tips.py.
 */
export interface GameWithTip {
  id: number
  slug: string
  squiggle_id: number | null
  round_id: number
  season: number
  home_team: string | null
  away_team: string | null
  home_score: number | null
  away_score: number | null
  venue: string | null
  date: string | null
  completed: boolean
  tip: Tip | null
  model_predictions: ModelPrediction[]
}

export interface GamesWithTipsResponse {
  games: GameWithTip[]
  count: number
}

/**
 * FX-11: HTTP status codes that we consider transient and worth retrying.
 */
const TRANSIENT_STATUSES = new Set([502, 503, 504])

/**
 * Default retry policy for transient failures (502/503/504 + network timeouts).
 * Exponential backoff with full jitter.
 */
const DEFAULT_RETRY_OPTIONS = {
  maxAttempts: 3,
  baseDelayMs: 200,
  maxDelayMs: 2000,
} as const

/**
 * Mirrors the round locator returned by `GET /api/games?latest=true`.
 * `is_post_season` is additive (grand-final uplift, 2026-09): true once
 * every game of the latest round is completed — the GF has been played.
 */
export interface LatestRoundResponse {
  season: number | null
  round_id: number | null
  game_count: number
  is_current_year: boolean
  has_upcoming: boolean
  is_grand_final: boolean
  is_post_season: boolean
  is_off_season: boolean
  premier: string | null
}

// ---------------------------------------------------------------------------
// Grand-final pre-match report (grand-final uplift, 2026-09).
// Mirrors backend/.../schemas/match_report.py field-for-field — do not
// rename: the payload keys come straight from the Pydantic models.
// ---------------------------------------------------------------------------

/** Mirrors `TeamStory`. */
export interface TeamStory {
  team: string
  narrative: string
  finals_path: string[]
}

/** Mirrors `PlayerSpotlight`. */
export interface PlayerSpotlight {
  name: string
  team: string
  note: string
}

/** Mirrors `InjuryNote`. */
export interface InjuryNote {
  player: string
  status: string
  note: string
}

/** Mirrors `SeasonStory` (home/away team narratives). */
export interface SeasonStory {
  home: TeamStory
  away: TeamStory
}

/** Mirrors `TeamPlayers` (home/away player spotlights). */
export interface TeamPlayers {
  home: PlayerSpotlight[]
  away: PlayerSpotlight[]
}

/** Mirrors `InjuryWatch` (home/away injury notes). */
export interface InjuryWatch {
  home: InjuryNote[]
  away: InjuryNote[]
}

/** Mirrors `ModelConsensus`. */
export interface ModelConsensus {
  summary: string
  models_picking_home: number
  models_picking_away: number
  season_accuracy_note: string
}

/** Mirrors `Prediction` (pre-match verdict; confidence is 0–1). */
export interface Prediction {
  winner: string
  margin: number
  confidence: number
}

/** Mirrors `GrandFinalReport` — the structured report payload. */
export interface GrandFinalReport {
  headline: string
  executive_summary: string
  season_story: SeasonStory
  keys_to_the_game: string[]
  key_players: TeamPlayers
  injury_watch: InjuryWatch
  model_consensus: ModelConsensus
  // GF-CONTENT: null when the agent's data source was empty — the
  // component hides the section instead of showing filler prose.
  weather_impact: string | null
  x_factor: string | null
  prediction: Prediction
  talking_points: string[]
}

/** Mirrors `MatchReportResponse` — envelope for `GET /api/games/{slug}/report`. */
export interface MatchReportResponse {
  id: number
  game_id: number
  report_type: string
  report: GrandFinalReport
  created_at: string
}

// ---------------------------------------------------------------------------
// Multi-league read side (ADR 0001 read APIs, 2026-09).
// Mirrors backend/.../schemas/events.py and the /api/sports payload —
// do not rename: the payload keys come straight from the Pydantic models.
// ---------------------------------------------------------------------------

/** One side of an event (mirrors `EventParticipantResponse`). */
export interface EventParticipant {
  side: string // home | away | n/a
  participant_name: string
  score: number | null
  is_winner: boolean | null
}

/** An event plus its participants (mirrors `EventResponse`). */
export interface SportEvent {
  id: number
  slug: string
  /** Round/week number; NULL for tournaments and race events. */
  round_id: number | null
  venue: string | null
  /** Venue-local naive timestamp (competition timezone policy). */
  starts_at: string | null
  status: string // scheduled | completed | cancelled | void
  completed: boolean
  competition: string
  season: string
  participants: EventParticipant[]
}

/** Mirrors `EventListResponse` — envelope for `GET /api/events`. */
export interface EventListResponse {
  events: SportEvent[]
  count: number
}

// LEAGUE-ROUTES (2026-09-30, user request): `GET /api/events/{slug}` returns
// the backend `EventResponse`, whose shape is exactly `SportEvent` above
// (field-for-field with backend/.../schemas/events.py).  The alias gives the
// match pages a named contract without duplicating the shape — if the
// backend payload ever diverges from the list-item shape, this is the one
// place to split it.
export type EventDetailResponse = SportEvent

/** A season of a competition (entries of `/api/sports` `seasons`). */
export interface CompetitionSeason {
  id: number
  label: string
  start_date: string | null
  end_date: string | null
  is_current: boolean
}

/** Mirrors the `/api/sports` competitions entries. */
export interface CompetitionInfo {
  id: number
  sport_id: string
  name: string
  tier: string
  format: string
  timezone: string
  seasons: CompetitionSeason[]
}

/** Mirrors the `/api/sports` sports entries. */
export interface SportInfo {
  id: string
  display_name: string
  competitions: CompetitionInfo[]
}

/** Mirrors `SportsListResponse` — envelope for `GET /api/sports`. */
export interface SportsListResponse {
  sports: SportInfo[]
}

// ---------------------------------------------------------------------------
// TEAM-IDENTITY (2026-09-30, user request): club crest/colour identity for
// the badge chain (useTeamIdentity). Mirrors the frozen
// `TeamsIdentityResponse` Pydantic shape (backend/.../schemas/teams.py)
// field-for-field — do not rename: the payload keys come straight from
// the backend models. Identity columns are nullable (LEFT-JOIN to the
// teams extension row): a team without identity data is still listed and
// the frontend's populate step skips it.
// ---------------------------------------------------------------------------

/** Mirrors `TeamIdentityResponse` — one `kind='team'` participant. */
export interface TeamIdentityPayload {
  name: string
  abbreviation?: string | null
  logo_url?: string | null
  primary_color?: string | null
  secondary_color?: string | null
}

/** Mirrors `TeamsIdentityResponse` — envelope for `GET /api/teams`. */
export interface TeamsIdentityResponse {
  teams: TeamIdentityPayload[]
}

// ---------------------------------------------------------------------------
// Boosted Tip (XGBoost) + SHAP (BT-1, boosted-tip feature).  Mirrors the
// dict payloads returned by backend/.../services/boosted_explanations.py
// field-for-field — do not rename: the payload keys come straight from
// the service.  NOTE: unlike GET /active-model (which wraps its payload
// in `{active, model}`), BOTH boosted endpoints return the payload
// directly and map "no active model / no explanation for the game" to
// 404 — callers catch and degrade (the section simply stays hidden).
// ---------------------------------------------------------------------------

/** One global SHAP importance row (mirrors `_enrich_importance`). */
export interface ShapImportanceEntry {
  feature_name: string
  /** Mean |SHAP value| across the training rows (global importance). */
  shap_value: number
  /** Underlying model the feature belongs to (feature-name convention). */
  model: string
  type: 'margin' | 'confidence' | 'other'
}

/** Mirrors `get_active_boosted_model` — active `boosted_tip` version. */
export interface ActiveBoostedModel {
  model_name: string
  version: number
  trained_at: string | null
  training_rows: number
  is_active: boolean
  /** JSONB metrics written by the weekly retrain (train-set r2/mae). */
  metrics: {
    r2: number | null
    mae: number | null
    /** TreeExplainer expected value — the SHAP bar chart's baseline. */
    shap_base_value: number | null
  }
  /** Sorted by |shap_value| descending, feature name as tie-break. */
  importances: ShapImportanceEntry[]
}

// ---------------------------------------------------------------------------
// PERF-PER-LEAGUE (2026-10): league-aware backtest payloads (D4). The
// league endpoints reuse the legacy AFL response shapes with ONE twist:
// state-league season labels are strings ('2026'), not ints, so every
// season field widens to `number | string` and /seasons labels to
// `(number | string)[]`. Field-for-field with the backend league backtest
// router — do not rename: the payload keys come straight from the API.
// ---------------------------------------------------------------------------

/** Mirrors `GET /api/backtest/seasons` — AFL returns int years, leagues return label strings. */
export interface LeagueSeasonsResponse {
  available_years: (number | string)[]
  /** AFL: calendar-year int. Leagues: the season LABEL string (league
   *  seasons carry no numeric id), null when the competition has no
   *  seasons at all. Normalized by lib/performanceSeasons consumers. */
  current_year: number | string | null
}

/** Mirrors `CurrentSeasonHeuristicPerformance` (same shape for AFL and leagues). */
export interface LeagueCurrentSeasonHeuristicPerformance {
  heuristic: string
  total_profit: number
  total_accuracy: number
  rounds_played: number
  avg_profit_per_round: number
  projected_annual_profit: number
  /** Share of tips settled at real bookmaker odds (0 for state leagues — fallback price applies). */
  odds_coverage: number
}

/** Mirrors `CurrentSeasonResponse` with the season label widened to a string. */
export interface LeagueCurrentSeasonResponse {
  season: number | string
  heuristics: LeagueCurrentSeasonHeuristicPerformance[]
  rounds_completed: number
  total_rounds: number
}

/** One per-heuristic entry of the comparison dict (mirrors `compare_heuristics` stats). */
export interface LeagueHeuristicSeasonStats {
  total_rounds: number
  total_tips: number
  total_correct: number
  overall_accuracy: number
  total_profit: number
  avg_profit_per_round: number
  best_round_accuracy: number
  worst_round_accuracy: number
  odds_coverage: number
}

/** Mirrors `GET /api/backtest/compare` — season label widened to a string for leagues. */
export interface LeagueComparisonResponse {
  season: number | string
  comparison: Record<string, LeagueHeuristicSeasonStats>
  best_overall: {
    heuristic: string | null
    accuracy: number
    profit: number
  }
}

export const useApi = () => {
  const config = useRuntimeConfig()
  const apiBase = config.public.apiBase as string

  /**
   * Resolve a logical API path to the full URL.
   *
   * FIX H-4 (2026-09 review): the dead FaaS routing block (fnUrlMap /
   * isFaasMode / prefix rewriting) read runtime-config keys that have
   * not existed since Phase 4 — `isFaasMode` was always false and the
   * `as string` casts hid undefined values.  The backend is a single
   * FastAPI origin; resolution is a plain base+path join.
   */
  const resolveUrl = (path: string): string => `${apiBase}${path}`

  /**
   * FX-11: Sleep helper used between retry attempts.
   */
  const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

  /**
   * FX-11: Backoff with full jitter — random delay in [0, capped].
   * `Full jitter` (per AWS architecture blog) gives better aggregate
   * behaviour under contention than equal or exponential backoff.
   */
  const backoffMs = (attempt: number): number => {
    const exp = Math.min(
      DEFAULT_RETRY_OPTIONS.maxDelayMs,
      DEFAULT_RETRY_OPTIONS.baseDelayMs * 2 ** attempt,
    )
    return Math.floor(Math.random() * exp)
  }

  const isTransient = (response: Response | null, error: unknown): boolean => {
    if (response && TRANSIENT_STATUSES.has(response.status)) return true
    if (error instanceof Error) {
      // AbortError = our timeout, DOMException for network failures
      if (error.name === 'AbortError') return true
      if (error.name === 'TypeError' && /fetch|network|failed/i.test(error.message)) return true
    }
    return false
  }

  /**
   * Fetch with a per-request timeout, plus retry with exponential
   * backoff for transient failures (502/503/504 + network/timeout).
   * 4xx responses are NOT retried (they are caller errors).
   */
  const fetchWithTimeout = async (
    url: string,
    options: RequestInit = {},
    timeout = 10000,
  ): Promise<Response> => {
    let lastError: unknown = null
    let lastResponse: Response | null = null

    for (let attempt = 0; attempt < DEFAULT_RETRY_OPTIONS.maxAttempts; attempt++) {
      const controller = new AbortController()
      const id = setTimeout(() => controller.abort(), timeout)

      try {
        const resolved = resolveUrl(url)
        const response = await fetch(resolved, {
          ...options,
          signal: controller.signal,
        })
        clearTimeout(id)

        if (response.ok) return response
        if (!isTransient(response, null)) return response
        lastResponse = response
        lastError = new Error(`Transient HTTP ${response.status}`)
      } catch (error) {
        clearTimeout(id)
        if (!isTransient(null, error)) throw error
        lastError = error
      }

      // Don't sleep after the final attempt.
      if (attempt < DEFAULT_RETRY_OPTIONS.maxAttempts - 1) {
        await sleep(backoffMs(attempt))
      }
    }

    // Exhausted retries — surface the last transient failure to the caller.
    if (lastResponse) return lastResponse
    throw lastError ?? new Error('useApi: transient retry budget exhausted')
  }

  // Games
  const getGames = async (params?: { season?: number; round?: number; upcoming?: boolean; latest?: boolean }) => {
    const queryParams = new URLSearchParams()
    if (params?.season) queryParams.append('season', params.season.toString())
    if (params?.round) queryParams.append('round', params.round.toString())
    if (params?.upcoming) queryParams.append('upcoming', 'true')
    if (params?.latest) queryParams.append('latest', 'true')

    const response = await fetchWithTimeout(`/api/games?${queryParams}`)
    if (!response.ok) throw new Error('Failed to fetch games')
    return response.json()
  }

  const getLatestRound = async (): Promise<LatestRoundResponse> => {
    const response = await fetchWithTimeout('/api/games?latest=true')
    if (!response.ok) throw new Error('Failed to fetch latest round')
    return response.json()
  }

  const getGame = async (slug: string) => {
    const response = await fetchWithTimeout(`/api/games/${slug}`)
    if (!response.ok) throw new Error('Failed to fetch game')
    return response.json()
  }

  const getGameDetail = async (slug: string): Promise<GameDetailResponse> => {
    const response = await fetchWithTimeout(`/api/games/${slug}/detail`)
    if (!response.ok) throw new Error('Failed to fetch game detail')
    return response.json()
  }

  /**
   * Grand-final pre-match report (grand-final uplift, 2026-09).
   * 404 means "no report for this game" (non-GF game, or the report
   * hasn't been generated yet) — that is an expected, non-error state,
   * so it maps to `null` instead of throwing.  Other non-OK responses
   * surface as errors like the rest of the composable.
   */
  const getGameReport = async (slug: string): Promise<MatchReportResponse | null> => {
    const response = await fetchWithTimeout(`/api/games/${slug}/report`)
    if (response.status === 404) return null
    if (!response.ok) throw new Error('Failed to fetch match report')
    return response.json()
  }

  // Tips
  const getTips = async (params?: { heuristic?: string; season?: number; round?: number }) => {
    const queryParams = new URLSearchParams()
    if (params?.heuristic) queryParams.append('heuristic', params.heuristic)
    if (params?.season) queryParams.append('season', params.season.toString())
    if (params?.round) queryParams.append('round', params.round.toString())

    const response = await fetchWithTimeout(`/api/tips?${queryParams}`)
    if (!response.ok) throw new Error('Failed to fetch tips')
    return response.json()
  }

  const getTipsByHeuristic = async (heuristic: string, limit = 100) => {
    const response = await fetchWithTimeout(`/api/tips/${heuristic}?limit=${limit}`)
    if (!response.ok) throw new Error('Failed to fetch tips')
    return response.json()
  }

  // NOTE (2026-09): `generateTips` has been REMOVED.  The generate
  // endpoint requires the admin X-API-Key (TIPS-GEN-H4) — a browser
  // must never hold that key.  Generation is the nightly
  // `tip-generation` cron's job; operators use the admin API directly.

  const getGamesWithTips = async (season: number, round: number, heuristic: string = 'best_bet'): Promise<GamesWithTipsResponse> => {
    const queryParams = new URLSearchParams()
    queryParams.append('season', season.toString())
    queryParams.append('round', round.toString())
    queryParams.append('heuristic', heuristic)

    const response = await fetchWithTimeout(`/api/tips/games-with-tips?${queryParams}`)
    if (!response.ok) throw new Error('Failed to fetch games with tips')
    return response.json()
  }

  // Backtest
  const getBacktestResults = async (params?: { heuristic?: string; season?: number }) => {
    const queryParams = new URLSearchParams()
    if (params?.heuristic) queryParams.append('heuristic', params.heuristic)
    if (params?.season) queryParams.append('season', params.season.toString())

    const response = await fetchWithTimeout(`/api/backtest?${queryParams}`)
    if (!response.ok) throw new Error('Failed to fetch backtest results')
    return response.json()
  }

  const runBacktest = async (season: number, round?: number, heuristic?: string) => {
    const queryParams = new URLSearchParams()
    queryParams.append('season', season.toString())
    if (round) queryParams.append('round', round.toString())
    if (heuristic) queryParams.append('heuristic', heuristic)

    const response = await fetchWithTimeout(`/api/backtest/run?${queryParams}`, {
      method: 'POST',
    })
    if (!response.ok) throw new Error('Failed to run backtest')
    return response.json()
  }

  const compareHeuristics = async (season: number) => {
    const response = await fetchWithTimeout(`/api/backtest/compare?season=${season}`)
    if (!response.ok) throw new Error('Failed to compare heuristics')
    return response.json()
  }

  const getAvailableSeasons = async () => {
    const response = await fetchWithTimeout('/api/backtest/seasons')
    if (!response.ok) throw new Error('Failed to fetch available seasons')
    return response.json()
  }

  const getBacktestTableData = async (season: number) => {
    const response = await fetchWithTimeout(`/api/backtest/table?season=${season}`)
    if (!response.ok) throw new Error('Failed to fetch backtest table data')
    return response.json()
  }

  const getCurrentSeasonPerformance = async () => {
    const response = await fetchWithTimeout('/api/backtest/current-season')
    if (!response.ok) throw new Error('Failed to fetch current season performance')
    return response.json()
  }

  const compareModels = async (season: number) => {
    const response = await fetchWithTimeout(`/api/backtest/model-compare?season=${season}`)
    if (!response.ok) throw new Error('Failed to compare models')
    return response.json()
  }

  const getActiveModel = async () => {
    const response = await fetchWithTimeout('/api/backtest/active-model')
    if (!response.ok) throw new Error('Failed to fetch active model')
    return response.json()
  }

  // BT-1 (boosted-tip): active boosted_tip (XGBoost) version + global
  // SHAP importances.  404 = no active model yet (pre-first-retrain or
  // BOOSTED_RETRAIN_ENABLED=false) — throws like getActiveModel; the
  // backtest section catches and hides itself.
  const getActiveBoostedModel = async (): Promise<ActiveBoostedModel> => {
    const response = await fetchWithTimeout('/api/backtest/active-boosted-model')
    if (!response.ok) throw new Error('Failed to fetch active boosted model')
    return response.json()
  }

  // PERF-PER-LEAGUE (2026-10): league-aware backtest variants (D4/D5).
  // `league` is OPTIONAL everywhere: omitted AND 'afl' map to the legacy
  // AFL path with the query param omitted entirely — byte-identical to the
  // methods above (frozen contract, no empty league param). Pure query
  // builder, no state — prerender-safe like the rest of the composable.
  const leagueQueryString = (league?: string): string => {
    const queryParams = new URLSearchParams()
    if (league && league !== 'afl') queryParams.append('league', league)
    return queryParams.toString()
  }

  const getLeagueSeasons = async (league?: string): Promise<LeagueSeasonsResponse> => {
    const queryString = leagueQueryString(league)
    const response = await fetchWithTimeout(
      queryString ? `/api/backtest/seasons?${queryString}` : '/api/backtest/seasons',
    )
    if (!response.ok) throw new Error('Failed to fetch available seasons')
    return response.json()
  }

  const getLeagueCurrentSeasonPerformance = async (
    league?: string,
  ): Promise<LeagueCurrentSeasonResponse> => {
    const queryString = leagueQueryString(league)
    const response = await fetchWithTimeout(
      queryString ? `/api/backtest/current-season?${queryString}` : '/api/backtest/current-season',
    )
    if (!response.ok) throw new Error('Failed to fetch current season performance')
    return response.json()
  }

  const getLeagueComparison = async (
    league?: string,
    season?: number,
  ): Promise<LeagueComparisonResponse> => {
    const queryParams = new URLSearchParams()
    if (season !== undefined) queryParams.append('season', season.toString())
    if (league && league !== 'afl') queryParams.append('league', league)
    const queryString = queryParams.toString()
    const response = await fetchWithTimeout(
      queryString ? `/api/backtest/compare?${queryString}` : '/api/backtest/compare',
    )
    if (!response.ok) throw new Error('Failed to compare heuristics')
    return response.json()
  }

  // Multi-league read side (ADR 0001): sport/competition discovery and
  // the event list per competition season. The 404 "unknown competition
  // or season" case THROWS like every other non-OK response — callers
  // that expect absence (leagues not synced yet) catch and degrade.
  const getSports = async (): Promise<SportsListResponse> => {
    const response = await fetchWithTimeout('/api/sports')
    if (!response.ok) throw new Error('Failed to fetch sports')
    return response.json()
  }

  const getEvents = async (params: {
    competition: number
    season: string
    round?: number
    limit?: number
  }): Promise<EventListResponse> => {
    const queryParams = new URLSearchParams()
    queryParams.append('competition', params.competition.toString())
    queryParams.append('season', params.season)
    if (params.round) queryParams.append('round', params.round.toString())
    if (params.limit) queryParams.append('limit', params.limit.toString())

    const response = await fetchWithTimeout(`/api/events?${queryParams}`)
    if (!response.ok) throw new Error('Failed to fetch events')
    return response.json()
  }

  // LEAGUE-ROUTES (2026-09-30, user request): single-event fetch for the
  // `/{league}/match/{slug}` pages.  Same convention as getSports/getEvents:
  // the 404 "unknown slug" case THROWS like every other non-OK response —
  // the match page (validate()/error handling) owns the not-found
  // presentation, so no getGameReport-style null mapping here.
  const getEvent = async (slug: string): Promise<EventDetailResponse> => {
    const response = await fetchWithTimeout(`/api/events/${slug}`)
    if (!response.ok) throw new Error('Failed to fetch event')
    return response.json()
  }

  // TEAM-IDENTITY (2026-09-30, user request): club identity fetch for the
  // badge chain. Frozen contract: `sport` is OPTIONAL (omit = all sports),
  // so the query string is appended ONLY when a filter is given — never a
  // bare trailing '?'. Non-OK THROWS like getSports; the degradation
  // (catch → badge fallback) is syncTeamIdentity's contract, not this
  // method's.
  const getTeams = async (sport?: string): Promise<TeamsIdentityResponse> => {
    const queryParams = new URLSearchParams()
    if (sport) queryParams.append('sport', sport)
    const queryString = queryParams.toString()
    const response = await fetchWithTimeout(
      queryString ? `/api/teams?${queryString}` : '/api/teams',
    )
    if (!response.ok) throw new Error('Failed to fetch teams')
    return response.json()
  }

  return {
    getGames,
    getGame,
    getGameDetail,
    getGameReport,
    getLatestRound,
    getTips,
    getTipsByHeuristic,
    getGamesWithTips,
    getBacktestResults,
    runBacktest,
    compareHeuristics,
    getAvailableSeasons,
    getBacktestTableData,
    getCurrentSeasonPerformance,
    compareModels,
    getActiveModel,
    getActiveBoostedModel,
    getLeagueSeasons,
    getLeagueCurrentSeasonPerformance,
    getLeagueComparison,
    getSports,
    getEvents,
    getEvent,
    getTeams,
  }
}
