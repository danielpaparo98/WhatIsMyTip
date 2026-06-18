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
  squiggle_id: number
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
  squiggle_id: number
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
 * Route prefix → FaaS function URL mapping.
 * Built once from Nuxt runtime config. When any FaaS URL is present,
 * API calls are routed to the correct function instead of the monolithic
 * apiBase URL.
 */
type FnUrlMap = Record<string, string>

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

export const useApi = () => {
  const config = useRuntimeConfig()
  const apiBase = config.public.apiBase as string

  // Build FaaS function URL map from runtime config
  const fnUrlMap: FnUrlMap = {
    '/api/games': config.public.gamesFnUrl as string,
    '/api/tips': config.public.tipsFnUrl as string,
    '/api/backtest': config.public.backtestFnUrl as string,
    '/api/admin': config.public.adminFnUrl as string,
  }

  // FaaS mode is active when at least one function URL is configured
  const isFaasMode = Object.values(fnUrlMap).some((url) => url && url.length > 0)

  /**
   * Resolve a logical API path to the correct full URL.
   *
   * In legacy mode: `{apiBase}/api/games/...`
   * In FaaS mode:   `{gamesFnUrl}/...` (strips the `/api/games` prefix)
   */
  const resolveUrl = (path: string): string => {
    if (isFaasMode) {
      // Sort prefixes longest-first so `/api/games` doesn't match before `/api/games-with-tips`
      const sortedPrefixes = Object.keys(fnUrlMap).sort((a, b) => b.length - a.length)
      for (const prefix of sortedPrefixes) {
        const fnUrl = fnUrlMap[prefix]
        if (fnUrl && path.startsWith(prefix)) {
          const subPath = path.slice(prefix.length)
          return `${fnUrl}${subPath}`
        }
      }
    }
    // Legacy mode or unmatched route — use monolithic backend
    return `${apiBase}${path}`
  }

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

  const getLatestRound = async () => {
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

  const generateTips = async (season: number, round: number, heuristics?: string[]) => {
    const queryParams = new URLSearchParams()
    queryParams.append('season', season.toString())
    queryParams.append('round', round.toString())
    if (heuristics) {
      heuristics.forEach(h => queryParams.append('heuristics', h))
    }

    const response = await fetchWithTimeout(`/api/tips/generate?${queryParams}`, {
      method: 'POST',
    })
    if (!response.ok) throw new Error('Failed to generate tips')
    return response.json()
  }

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

  return {
    getGames,
    getGame,
    getGameDetail,
    getLatestRound,
    getTips,
    getTipsByHeuristic,
    generateTips,
    getGamesWithTips,
    getBacktestResults,
    runBacktest,
    compareHeuristics,
    getAvailableSeasons,
    getBacktestTableData,
    getCurrentSeasonPerformance,
  }
}
