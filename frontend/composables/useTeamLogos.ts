// Mapping from AFL team name (as returned by the backend) → logo
// filename in /public/logos.
//
// The backend normalises human-friendly aliases (e.g. "Brisbane Lions",
// "Western Bulldogs", "Gold Coast", "St Kilda") to the canonical
// Squiggle form ("Brisbane", "Bulldogs", "GoldCoast", "StKilda") via
// _canonical_team() in backend/scripts/load_csv_to_db.py.  The keys
// below MUST match that canonical form, otherwise the logo lookup
// silently returns "" and the page renders a broken <img> tag.
//
// CR-005 from Phase 2b: the previous version used the human-friendly
// aliases as keys, which meant 9 of 18 logos failed to resolve at
// runtime.  See also tests/unit/useTeamLogos.test.ts which asserts
// every key matches a file that exists and aligns with the backend
// canonical names.
const TEAM_LOGOS: Record<string, string> = {
  Adelaide: 'Adelaide.png',
  Brisbane: 'Brisbane.png',
  Bulldogs: 'Bulldogs.png',
  Carlton: 'Carlton.png',
  Collingwood: 'Collingwood.png',
  Essendon: 'Essendon.png',
  Fremantle: 'Fremantle.png',
  Geelong: 'Geelong.png',
  Giants: 'Giants.png',
  GoldCoast: 'GoldCoast.png',
  Hawthorn: 'Hawthorn.png',
  Melbourne: 'Melbourne.png',
  NorthMelbourne: 'NorthMelbourne.png',
  PortAdelaide: 'PortAdelaide.png',
  Richmond: 'Richmond.png',
  StKilda: 'StKilda.png',
  Sydney: 'Sydney.png',
  WestCoast: 'WestCoast.png',
}

// Alias → canonical team name (mirrors backend/packages/shared/teams.py).
// Belt-and-suspenders normaliser so a stray non-canonical name (e.g. the
// raw Squiggle "Western Bulldogs" / "GWS" forms) still resolves to a logo
// even if the backend ever drifts.
const TEAM_ALIASES: Record<string, string> = {
  'adelaide crows': 'Adelaide',
  'brisbane lions': 'Brisbane',
  'fremantle dockers': 'Fremantle',
  gws: 'Giants',
  'greater western sydney': 'Giants',
  'gws giants': 'Giants',
  'gold coast': 'GoldCoast',
  'gold coast suns': 'GoldCoast',
  'north melbourne': 'NorthMelbourne',
  kangaroos: 'NorthMelbourne',
  'port adelaide': 'PortAdelaide',
  'port power': 'PortAdelaide',
  'st kilda': 'StKilda',
  'sydney swans': 'Sydney',
  'west coast': 'WestCoast',
  'west coast eagles': 'WestCoast',
  'western bulldogs': 'Bulldogs',
  footscray: 'Bulldogs',
}

function normalizeTeam(name: string): string {
  const key = name.trim().toLowerCase()
  return TEAM_ALIASES[key] ?? name.trim()
}

export function useTeamLogos() {
  /**
   * Resolve a team name to its public logo URL.
   * Accepts `null`/`undefined` and returns an empty string so callers
   * can safely bind the result to `<img :src="…">` without a runtime
   * TypeError.  Non-canonical aliases are normalised first so every
   * known AFL team renders its logo.
   */
  const getLogoUrl = (teamName: string | null | undefined): string => {
    if (!teamName) return ''
    const canonical = normalizeTeam(teamName)
    const filename = TEAM_LOGOS[canonical] || ''
    return filename ? `/logos/${filename}` : ''
  }

  return { getLogoUrl, TEAM_LOGOS, normalizeTeam }
}
