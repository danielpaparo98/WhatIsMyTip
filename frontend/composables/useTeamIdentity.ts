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
import { leagueColorFor, type ClubColors } from './useLeagueColors'
import { useTeamLogos } from './useTeamLogos'
// TEAM-IDENTITY (2026-09-30, user request): type-only import — the pure
// helpers below stay importable from vitest without the Nuxt runtime.
import type { TeamIdentityPayload } from './useApi'

export interface TeamIdentityEntry {
  logoUrl?: string
  primaryColor?: string
  secondaryColor?: string
}

export const TEAM_IDENTITY: Record<string, TeamIdentityEntry> = {}

// TEAM-IDENTITY (2026-09-30, user request): module-scoped one-shot guard
// for syncTeamIdentity, keyed on the sport filter used ('' = unfiltered).
// Exported so tests can reset it (vitest keeps module state within a
// file), mirroring how TEAM_IDENTITY itself is cleared in tests. A key
// is added BEFORE the fetch starts, so repeated visits AND concurrent
// callers share one fetch; a failed sync stays marked (one attempt per
// sport per tab — the badge fallback covers the gap until reload).
export const TEAM_IDENTITY_SYNCED = new Set<string>()

const { normalizeTeam } = useTeamLogos()

/**
 * TEAM-IDENTITY (2026-09-30, user request): merge the frozen
 * `GET /api/teams` payload into the module TEAM_IDENTITY map.
 *
 * - Keys go through normalizeTeam (shared with the logo map), so
 *   feed-name drift and AFL aliases resolve to the same entry
 *   identityFor() looks up.
 * - snake_case payload keys map to the camelCase entry shape
 *   (logo_url→logoUrl, primary_color→primaryColor, secondary_color→
 *   secondaryColor); null/empty identity fields are dropped.
 * - Entries with NO identity fields at all (the backend's LEFT-JOIN
 *   null rows) are skipped entirely — they must never shadow the
 *   badge fallback with an empty entry.
 * - Pure merge (never wipes existing keys) and idempotent: re-running
 *   the same payload leaves the map unchanged.
 *
 * Returns the number of entries that carried identity and were merged.
 */
export function populateTeamIdentity(entries: TeamIdentityPayload[]): number {
  let populated = 0
  for (const payloadEntry of entries) {
    const identity: TeamIdentityEntry = {}
    if (payloadEntry.logo_url) identity.logoUrl = payloadEntry.logo_url
    if (payloadEntry.primary_color) identity.primaryColor = payloadEntry.primary_color
    if (payloadEntry.secondary_color) identity.secondaryColor = payloadEntry.secondary_color
    if (Object.keys(identity).length === 0) continue
    const key = normalizeTeam(payloadEntry.name)
    if (!key) continue
    TEAM_IDENTITY[key] = identity
    populated++
  }
  return populated
}

/**
 * TEAM-IDENTITY (2026-09-30, user request): fetch `GET /api/teams` (sport
 * filter optional — omit for all sports) and populate TEAM_IDENTITY, once
 * per sport per tab (guarded by TEAM_IDENTITY_SYNCED, above).
 *
 * NEVER throws: every failure — network outage, non-OK response, missing
 * endpoint — resolves `false`, so league/match pages that await this
 * inside their data handlers keep rendering fixtures with the initials-
 * badge fallback. Resolves `true` when identity is available (fetched
 * now, or already synced by a previous call). Designed to be awaited
 * inside awaited useAsyncData handlers so crests bake into the
 * prerendered HTML (SEO-C1 contract).
 */
export function syncTeamIdentity(sport?: string): Promise<boolean> {
  const guardKey = sport ?? ''
  if (TEAM_IDENTITY_SYNCED.has(guardKey)) {
    return Promise.resolve(true)
  }
  TEAM_IDENTITY_SYNCED.add(guardKey)
  return (async () => {
    try {
      // useApi resolves through Nuxt's auto-import at call time (same
      // pattern as the other composables) — stubbed as a global in tests.
      const api = useApi()
      const payload = await api.getTeams(sport)
      populateTeamIdentity(payload?.teams ?? [])
      return true
    } catch {
      // Non-fatal by contract — badge fallback survives an API outage.
      return false
    }
  })()
}

// LEAGUE-ROUTES (2026-09-30, user request): real logo assets for
// state-league clubs are out of scope, so clubs without a logo file
// get a generated inline SVG initials badge instead of the neutral
// placeholder — club primary colour background, initials in a
// contrasting colour, on the same 40x40 canvas as PLACEHOLDER_LOGO in
// useTeamLogos.ts (identical data-URI encoding, no extra request).
// The generator is a pure module-level function so tests assert the
// output directly without mounting components, and the same
// (teamName, palette) always yields the identical data URI.

/** Max initials rendered — badges stay legible at 40px with two letters. */
const BADGE_MAX_INITIALS = 2

/** WCAG large-text contrast threshold; badge initials are bold 16px. */
const MIN_TEXT_CONTRAST = 3

/**
 * The first ALPHABETIC character of a word, or '' when it has none.
 */
const firstAlphaChar = (word: string): string =>
  word.match(/[A-Za-z]/)?.[0] ?? ''

/**
 * Deterministic initials for a club name: the first letter of each of
 * the first two whitespace-separated words ("Peel Thunder" → "PT");
 * single-word names take the first two characters ("Perth" → "PE").
 * Case/whitespace-insensitive so feed-name drift stays harmless;
 * hyphenated words count as one word ("Woodville-West Torrens" → "WT").
 *
 * LEAGUE-ROUTES (2026-09-30, code review): initials derive from
 * ALPHABETIC leading characters only. The output lands unescaped in
 * the badge SVG's text node (encodeURIComponent is transport-only,
 * not XML escaping), so a hostile feed name like "<script" used to
 * produce fatally invalid XML — now only [A-Za-z] characters can
 * reach the text node, and names with no letters at all yield ''
 * (initialsBadge then falls back to the neutral placeholder).
 */
export function initialsFor(teamName: string): string {
  const words = teamName.trim().split(/\s+/).filter((word) => word.length > 0)
  if (words.length === 0) return ''
  const first = words[0] ?? ''
  if (words.length === 1) {
    return [...first]
      .filter((ch) => /[A-Za-z]/.test(ch))
      .slice(0, BADGE_MAX_INITIALS)
      .join('')
      .toUpperCase()
  }
  const second = words[1] ?? ''
  return (firstAlphaChar(first) + firstAlphaChar(second)).toUpperCase()
}

const hexToRgb = (hex: string): [number, number, number] | null => {
  const match = /^#([0-9A-Fa-f]{6})$/.exec(hex.trim())
  const digits = match?.[1]
  if (!digits) return null
  const int = parseInt(digits, 16)
  return [(int >> 16) & 255, (int >> 8) & 255, int & 255]
}

const srgbChannelToLinear = (channel: number): number => {
  const s = channel / 255
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
}

const relativeLuminance = (rgb: [number, number, number]): number =>
  0.2126 * srgbChannelToLinear(rgb[0]) +
  0.7152 * srgbChannelToLinear(rgb[1]) +
  0.0722 * srgbChannelToLinear(rgb[2])

/** WCAG contrast ratio between two #rrggbb colours (range 1..21). */
const contrastRatio = (a: string, b: string): number => {
  const rgbA = hexToRgb(a)
  const rgbB = hexToRgb(b)
  if (!rgbA || !rgbB) return 1
  const lA = relativeLuminance(rgbA)
  const lB = relativeLuminance(rgbB)
  return (Math.max(lA, lB) + 0.05) / (Math.min(lA, lB) + 0.05)
}

/**
 * Initials colour: the club's own secondary when it reads against the
 * primary, otherwise black/white — whichever contrasts more. Palette
 * colours only, per the monochrome design constraint (no new accents).
 */
const badgeTextColor = (colors: ClubColors): string => {
  if (contrastRatio(colors.primary, colors.secondary) >= MIN_TEXT_CONTRAST) {
    return colors.secondary
  }
  const white = contrastRatio(colors.primary, '#FFFFFF')
  const black = contrastRatio(colors.primary, '#000000')
  return white >= black ? '#FFFFFF' : '#000000'
}

/**
 * Inline SVG initials badge as a data URI, or null when the name or
 * palette is missing (callers fall through to the neutral placeholder).
 * Output depends only on the derived initials and the palette, so it is
 * fully deterministic and testable without a DOM.
 */
export function initialsBadge(
  teamName: string | null | undefined,
  colors: ClubColors | null,
): string | null {
  if (!teamName || !colors) return null
  const initials = initialsFor(teamName)
  if (!initials) return null
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">' +
    `<rect width="40" height="40" fill="${colors.primary}"/>` +
    `<text x="20" y="26" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-weight="700" font-size="16" letter-spacing="1" fill="${badgeTextColor(colors)}">${initials}</text>` +
    '</svg>'
  return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg)
}

export function useTeamIdentity() {
  const { getLogoUrl, normalizeTeam, PLACEHOLDER_LOGO } = useTeamLogos()

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
   *
   * LEAGUE-ROUTES (2026-09-30, user request): when a league key is
   * supplied and the AFL fallback would be the neutral placeholder
   * (no logo file exists) AND leagueColorFor resolves the club, a
   * generated initials badge (see initialsBadge above) is returned
   * instead. Omitting the league key keeps byte-identical behaviour
   * for the existing AFL callers (index.vue / game page).
   */
  const logoFor = (
    teamName: string | null | undefined,
    leagueKey?: string | null,
  ): string | null => {
    const entry = identityFor(teamName)
    if (entry?.logoUrl) return entry.logoUrl
    const fallback = getLogoUrl(teamName)
    if (teamName && leagueKey && fallback === PLACEHOLDER_LOGO) {
      const badge = initialsBadge(teamName, leagueColorFor(leagueKey, teamName))
      if (badge) return badge
    }
    return fallback
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
