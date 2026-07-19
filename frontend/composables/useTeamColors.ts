/**
 * AFL team → primary colour hex values.
 *
 * Used by the confetti and off-season banner components so celebratory
 * effects use the premier's colours.  Keys match the canonical team
 * names in `useTeamLogos.ts` (which mirror the backend's
 * `packages/shared/teams.py` canonical form).
 *
 * Each entry lists 2–3 colours (primary kit colours) so `canvas-confetti`
 * can pick from a small palette rather than a single flat colour.
 */
const TEAM_COLORS: Record<string, string[]> = {
  Adelaide: ['#002B5C', '#E31937', '#F5C800'],
  Brisbane: ['#990033', '#F5C800', '#003D7A'],
  Bulldogs: ['#C8102E', '#003DA5', '#FFFFFF'],
  Carlton: ['#0C2F6B', '#FFFFFF'],
  Collingwood: ['#000000', '#FFFFFF'],
  Essendon: ['#CC0000', '#000000'],
  Fremantle: ['#2A0B4A', '#FFFFFF', '#EE1927'],
  Geelong: ['#003087', '#FFFFFF'],
  Giants: ['#F47920', '#333333', '#FFFFFF'],
  GoldCoast: ['#BA1F26', '#FFD700', '#0066B4'],
  Hawthorn: ['#49110A', '#F5C800'],
  Melbourne: ['#002B5C', '#E31937'],
  NorthMelbourne: ['#2B4C97', '#FFFFFF'],
  PortAdelaide: ['#000000', '#01A5B0', '#FFFFFF'],
  Richmond: ['#FFD700', '#000000'],
  StKilda: ['#ED0F05', '#000000', '#FFFFFF'],
  Sydney: ['#ED0F05', '#FFFFFF'],
  WestCoast: ['#003087', '#F5C800', '#FFFFFF'],
}

/** Fallback palette used when no team is known (grand final during the round). */
export const GRAND_FINAL_COLORS = ['#FFD700', '#FFC107', '#FFA000', '#FF8F00', '#FF6F00']

/** Fallback palette used when the premier team has no colour mapping. */
export const DEFAULT_CELEBRATION_COLORS = ['#FFD700', '#FFC107', '#FFA000']

/**
 * Resolve a canonical team name to its primary colour palette.
 * Falls back to the default celebration colours for unknown teams.
 */
export function getTeamColors(teamName: string | null | undefined): string[] {
  if (!teamName) return DEFAULT_CELEBRATION_COLORS
  return TEAM_COLORS[teamName] ?? DEFAULT_CELEBRATION_COLORS
}

export function useTeamColors() {
  return { getTeamColors, TEAM_COLORS, GRAND_FINAL_COLORS, DEFAULT_CELEBRATION_COLORS }
}
