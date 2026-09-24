// Data-driven team identity (backend migration 0011).
//
// TEAM_IDENTITY is where logo URLs / colours captured at ingestion
// land: the backend stores them per team (teams.logo_url,
// primary_color, secondary_color) and a future API surface exposes
// them here.  The map ships EMPTY — the hard-coded AFL maps in
// useTeamLogos/useTeamColors remain the fallback for every AFL club,
// and anything without an explicit entry resolves exactly as before.
//
// Expected shape of an entry mirrors the backend columns; keys match
// the team name the backend returns (canonical AFL form or the raw
// feed name for state-league clubs).
import { getTeamColors } from './useTeamColors'
import { leagueColorFor } from './useLeagueColors'
import { useTeamLogos } from './useTeamLogos'

export interface TeamIdentityEntry {
  logoUrl?: string
  primaryColor?: string
  secondaryColor?: string
}

export const TEAM_IDENTITY: Record<string, TeamIdentityEntry> = {}

export function useTeamIdentity() {
  const { getLogoUrl, normalizeTeam } = useTeamLogos()

  /**
   * The identity entry for a team, or null when none is on file.
   * Alias normalisation is shared with the logo map so an entry keyed
   * canonically ("Brisbane") also serves raw forms ("Brisbane Lions").
   */
  const identityFor = (
    teamName: string | null | undefined,
  ): TeamIdentityEntry | null => {
    if (!teamName) return null
    return TEAM_IDENTITY[normalizeTeam(teamName)] ?? null
  }

  /**
   * Logo URL for a team: an explicit identity entry wins, otherwise
   * the existing AFL fallback (incl. the inline placeholder for
   * unknown teams).
   */
  const logoFor = (teamName: string | null | undefined): string | null => {
    const entry = identityFor(teamName)
    if (entry?.logoUrl) return entry.logoUrl
    return getLogoUrl(teamName)
  }

  /**
   * Colour for a team at a palette index (0 = primary, 1 = secondary).
   * Resolution order: an explicit identity entry, then the active
   * league's curated club palette (useLeagueColors, non-AFL leagues),
   * then the AFL colour map (which itself has a default celebration
   * palette).  Each stage returns null past its palette length rather
   * than silently mixing palettes.
   */
  const colorFor = (
    teamName: string | null | undefined,
    idx: number,
    activeLeague?: string | null,
  ): string | null => {
    const entry = identityFor(teamName)
    if (entry?.primaryColor || entry?.secondaryColor) {
      const palette = [entry.primaryColor, entry.secondaryColor].filter(
        (c): c is string => typeof c === 'string' && c.length > 0,
      )
      return palette[idx] ?? null
    }
    const club = leagueColorFor(activeLeague, teamName)
    if (club) {
      return [club.primary, club.secondary][idx] ?? null
    }
    return getTeamColors(teamName)[idx] ?? null
  }

  return { logoFor, colorFor, identityFor, TEAM_IDENTITY }
}
