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

export function useTeamLogos() {
  const getLogoUrl = (teamName: string): string => {
    const filename = TEAM_LOGOS[teamName] || ''
    return filename ? `/logos/${filename}` : ''
  }

  return { getLogoUrl, TEAM_LOGOS }
}
