export const useApi = () => {
  const config = useRuntimeConfig()
  const apiBase = config.public.apiBase
  
  const fetchWithTimeout = async (
    url: string,
    options: RequestInit = {},
    timeout = 10000
  ) => {
    const controller = new AbortController()
    const id = setTimeout(() => controller.abort(), timeout)
    
    try {
      const response = await fetch(`${apiBase}${url}`, {
        ...options,
        signal: controller.signal,
      })
      clearTimeout(id)
      return response
    } catch (error) {
      clearTimeout(id)
      throw error
    }
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
  
  const getGame = async (gameId: number) => {
    const response = await fetchWithTimeout(`/api/games/${gameId}`)
    if (!response.ok) throw new Error('Failed to fetch game')
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
  
  return {
    getGames,
    getGame,
    getLatestRound,
    getTips,
    getTipsByHeuristic,
    generateTips,
    getBacktestResults,
    runBacktest,
    compareHeuristics,
  }
}
