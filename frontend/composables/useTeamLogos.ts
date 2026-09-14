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

// M-1 (2026-09 review): an inline neutral placeholder (data URI, no
// extra request).  Previously unknown/placeholder teams ('TBD', null)
// resolved to '' and pages rendered <img src=""> — per spec an empty
// src resolves to the CURRENT DOCUMENT URL, so the browser fetched the
// page's own HTML as an image (broken-image icon + wasted request).
// The placeholder is a 40x40 monochrome silhouette matching the site's
// bold monochrome design.
const PLACEHOLDER_LOGO =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">' +
      '<rect width="40" height="40" fill="#f5f5f5"/>' +
      '<ellipse cx="20" cy="20" rx="13" ry="17" fill="none" stroke="#666666" stroke-width="2"/>' +
      '<line x1="20" y1="3" x2="20" y2="37" stroke="#666666" stroke-width="2"/>' +
      '</svg>'
  )

// DESIGN-FIX: the backend stores canonical Squiggle names WITHOUT
// spaces (NorthMelbourne, PortAdelaide, GoldCoast, StKilda, Giants,
// Bulldogs) — correct for lookups, wrong for humans. Display-only map;
// data keys stay canonical.
const TEAM_DISPLAY: Record<string, string> = {
  NorthMelbourne: 'North Melbourne',
  PortAdelaide: 'Port Adelaide',
  GoldCoast: 'Gold Coast',
  StKilda: 'St Kilda',
  Giants: 'GWS Giants',
  Bulldogs: 'Western Bulldogs',
}

export function useTeamLogos() {
  /**
   * Human-friendly display name for a canonical team name.
   * Falls back to the input when no display mapping exists.
   */
  const getTeamDisplayName = (teamName: string | null | undefined): string => {
    if (!teamName) return 'TBD'
    return TEAM_DISPLAY[teamName] ?? teamName
  }

  /**
   * Resolve a team name to its public logo URL.
   * Accepts `null`/`undefined`; unknown or placeholder teams ('TBD')
   * return an inline placeholder SVG (never an empty string — see M-1).
   * Non-canonical aliases are normalised first so every known AFL team
   * renders its logo.
   */
  const getLogoUrl = (teamName: string | null | undefined): string => {
    if (!teamName) return PLACEHOLDER_LOGO
    const canonical = normalizeTeam(teamName)
    const filename = TEAM_LOGOS[canonical] || ''
    return filename ? `/logos/${filename}` : PLACEHOLDER_LOGO
  }

  return { getLogoUrl, getTeamDisplayName, TEAM_LOGOS, normalizeTeam, PLACEHOLDER_LOGO }
}
