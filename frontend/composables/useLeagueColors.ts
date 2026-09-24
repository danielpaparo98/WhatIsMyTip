// Per-club brand colours for the non-AFL leagues (state-league rollout).
//
// LEAGUE_COLORS maps a backend league key (packages/shared/ingestion/
// state_leagues.py STATE_LEAGUES) → club name → {primary, secondary}.
// Keys are the raw feed club names (Sportix "Peel Thunder", AFL
// platform "Box Hill Hawks", …); leagueColorFor matches
// case/whitespace-insensitively so feed drift stays harmless.
//
// These are HAND-CURATED from the clubs' official branding — logos are
// captured at ingestion (Sportix) but colours are not exposed by any
// feed. Entries without a TODO(colors) comment follow well-established
// club identities; commented ones are best-effort and worth verifying
// against current official style guides.
//
// AFL-affiliated entries reuse the palette from useTeamColors.ts so
// the reserves/VFL sides match their parent clubs exactly.

import { getTeamColors } from './useTeamColors'
import { useTeamLogos } from './useTeamLogos'

export interface ClubColors {
  primary: string
  secondary: string
}

export type LeaguePalette = Record<string, ClubColors>

const afl = (canonical: string): ClubColors => {
  const colors = getTeamColors(canonical)
  return { primary: colors[0], secondary: colors[1] }
}

// WAFL's ten clubs — shared by WAFLW (same clubs, women's competition).
const WAFL_CLUBS: LeaguePalette = {
  // TODO(colors): verify — navy/gold, exact shades unofficial.
  Claremont: { primary: '#002E5D', secondary: '#F2A900' },
  'East Fremantle': { primary: '#003DA5', secondary: '#FFFFFF' },
  // TODO(colors): verify — black with royal blue, exact blue unofficial.
  'East Perth': { primary: '#000000', secondary: '#2B4C97' },
  // TODO(colors): verify — navy/gold lightning branding.
  'Peel Thunder': { primary: '#1B3C8C', secondary: '#FFC655' },
  // TODO(colors): verify — red/black "Demons".
  Perth: { primary: '#C8102E', secondary: '#000000' },
  'South Fremantle': { primary: '#D71920', secondary: '#FFFFFF' },
  // TODO(colors): verify — maroon/gold "Lions".
  Subiaco: { primary: '#7A263A', secondary: '#FFC655' },
  'Swan Districts': { primary: '#000000', secondary: '#FFFFFF' },
  // TODO(colors): verify — cardinal red over navy.
  'West Perth': { primary: '#9E1B32', secondary: '#002B5C' },
  // West Coast reserves — parent-club palette.
  'West Coast': afl('WestCoast'),
}

const VFL_CLUBS: LeaguePalette = {
  'Box Hill Hawks': afl('Hawthorn'),
  // TODO(colors): verify — Melbourne-aligned, red over navy.
  'Casey Demons': { primary: '#002B5C', secondary: '#E31937' },
  Collingwood: afl('Collingwood'),
  Essendon: afl('Essendon'),
  // TODO(colors): verify — "Dolphins", commonly rendered purple/gold.
  Frankston: { primary: '#3F2A8C', secondary: '#FFC655' },
  Geelong: afl('Geelong'),
  'North Melbourne': afl('NorthMelbourne'),
  // TODO(colors): verify — "the Borough", blue/white.
  'Port Melbourne': { primary: '#003DA5', secondary: '#FFFFFF' },
  Richmond: afl('Richmond'),
  // TODO(colors): verify — "Zebras", red/white.
  Sandringham: { primary: '#D71920', secondary: '#FFFFFF' },
  // TODO(colors): verify — navy/gold/white shark branding.
  Southport: { primary: '#0C2F6B', secondary: '#F2C94C' },
  'Sydney Swans': afl('Sydney'),
  // TODO(colors): verify — "Tigers", gold/black stripes.
  Werribee: { primary: '#FFC655', secondary: '#000000' },
  // TODO(colors): verify — "Seagulls", navy/white.
  Williamstown: { primary: '#002B5C', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Lions", royal blue/white.
  Coburg: { primary: '#1D4E9E', secondary: '#FFFFFF' },
  'Brisbane Lions': afl('Brisbane'),
  Carlton: afl('Carlton'),
  // TODO(colors): verify — navy/white, Carlton-aligned.
  'Northern Bullants': { primary: '#14264E', secondary: '#FFFFFF' },
}

// VFLW shares most VFL clubs plus women's-only sides.
const VFLW_CLUBS: LeaguePalette = {
  ...VFL_CLUBS,
  // TODO(colors): verify — "Falcons", green/white.
  'Darebin Falcons': { primary: '#1B6B45', secondary: '#FFFFFF' },
  // TODO(colors): verify — St Kilda-aligned red/white/black.
  'Southern Saints': { primary: '#ED0F05', secondary: '#FFFFFF' },
}

const SANFL_CLUBS: LeaguePalette = {
  Adelaide: afl('Adelaide'),
  // TODO(colors): verify — "the Bays", navy/white.
  Glenelg: { primary: '#002E5D', secondary: '#FFFFFF' },
  'North Adelaide': { primary: '#D71920', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Redlegs", red over blue.
  Norwood: { primary: '#D71920', secondary: '#002B5C' },
  'Port Adelaide Magpies': { primary: '#000000', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Panthers", navy/white.
  'South Adelaide': { primary: '#002B5C', secondary: '#FFFFFF' },
  // "Double Blues" — royal over navy.
  Sturt: { primary: '#2B4C97', secondary: '#0C2F6B' },
  'West Adelaide': { primary: '#C8102E', secondary: '#000000' },
  // "Eagles" — navy/gold; AFL platform uses both spellings.
  'Woodville-West Torrens': { primary: '#003DA5', secondary: '#F2C94C' },
  Eagles: { primary: '#003DA5', secondary: '#F2C94C' },
  // TODO(colors): verify — red/white/blue "Bulldogs".
  'Central District': { primary: '#C8102E', secondary: '#002B5C' },
}

const QAFL_CLUBS: LeaguePalette = {
  // TODO(colors): verify — "Hornets", black/gold.
  'Aspley Hornets': { primary: '#000000', secondary: '#F2C94C' },
  // TODO(colors): verify — Geelong-aligned blue/white hoops.
  'Broadbeach Cats': { primary: '#003DA5', secondary: '#FFFFFF' },
  // TODO(colors): verify — black/white.
  'Coorparoo Kings': { primary: '#000000', secondary: '#FFFFFF' },
  'Gold Coast Suns': afl('GoldCoast'),
  'Brisbane Lions': afl('Brisbane'),
  // TODO(colors): verify — "Tigers", black/gold.
  'Labrador Tigers': { primary: '#000000', secondary: '#F2C94C' },
  // TODO(colors): verify — "Roos", blue/white.
  Maroochydore: { primary: '#2B4C97', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Panthers", black/white.
  'Morningside Panthers': { primary: '#000000', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Lions", maroon/gold.
  'Moreton Bay Lions': { primary: '#6E1E3C', secondary: '#F2C94C' },
  // TODO(colors): verify — "Vultures", maroon/white.
  'Mount Gravatt Vultures': { primary: '#6E1E3C', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Tigers", black/gold.
  'Noosa Tigers': { primary: '#000000', secondary: '#F2C94C' },
  // TODO(colors): verify — "Lions", maroon/gold.
  'Palm Beach Currumbin Lions': { primary: '#6E1E3C', secondary: '#F2C94C' },
  // TODO(colors): verify — "Sharks", navy/red.
  'Redland-Victoria Point Sharks': { primary: '#0C2F6B', secondary: '#D71920' },
  // TODO(colors): verify — "Magpies", black/white.
  'Sherwood Magpies': { primary: '#000000', secondary: '#FFFFFF' },
  Southport: VFL_CLUBS.Southport,
  // TODO(colors): verify — "Demons", red/blue.
  'Surfers Paradise Demons': { primary: '#D71920', secondary: '#002B5C' },
  // TODO(colors): verify — red/white.
  'UQ Red Lions': { primary: '#C8102E', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Gorillas", black/white.
  'Wilston Grange Gorillas': { primary: '#000000', secondary: '#FFFFFF' },
}

// Tasmania — TSL/legacy regional leagues, thinnest confidence of the
// set; every entry needs verification.
const TAS_MAN_CLUBS: LeaguePalette = {
  // TODO(colors): verify — "Magpies", black/white.
  Devonport: { primary: '#000000', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Robins", black/white.
  Ulverstone: { primary: '#000000', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Two Blues", navy/sky.
  Penguin: { primary: '#002B5C', secondary: '#7FB2E5' },
  // TODO(colors): verify — "Cats", blue/white.
  Wynyard: { primary: '#003DA5', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Demons", red/black.
  Latrobe: { primary: '#C8102E', secondary: '#000000' },
  // TODO(colors): verify — "Swans", red/white.
  'East Devonport': { primary: '#D71920', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Saints", blue/red.
  Smithton: { primary: '#2B4C97', secondary: '#D71920' },
}

const TAS_SOUTH_CLUBS: LeaguePalette = {
  // TODO(colors): verify — "Roos", blue/white.
  Clarence: { primary: '#002B5C', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Magpies", black/white with red trim.
  Glenorchy: { primary: '#000000', secondary: '#FFFFFF' },
  // TODO(colors): verify — "Tigers", black/gold.
  Kingborough: { primary: '#000000', secondary: '#F2C94C' },
  // TODO(colors): verify — "Demons", red/blue.
  'North Hobart': { primary: '#C8102E', secondary: '#002B5C' },
  // TODO(colors): verify — "Bombers", blue/red.
  Lauderdale: { primary: '#003DA5', secondary: '#D71920' },
  // TODO(colors): verify — "Eagles", blue/white.
  'New Norfolk': { primary: '#2B4C97', secondary: '#FFFFFF' },
}

export const LEAGUE_COLORS: Record<string, LeaguePalette> = {
  wafl: WAFL_CLUBS,
  waflw: WAFL_CLUBS,
  vfl: VFL_CLUBS,
  vflw: VFLW_CLUBS,
  sanfl: SANFL_CLUBS,
  qafl: QAFL_CLUBS,
  qaflw: QAFL_CLUBS,
  // AFLW sides are AFL clubs — reuse the canonical AFL palette.
  aflw: Object.fromEntries(
    Object.keys(useTeamLogos().TEAM_LOGOS).map((canonical) => [
      canonical,
      afl(canonical),
    ]),
  ),
  tsl: {
    // TODO(colors): verify — North Launceston "Bombers", black/gold.
    'North Launceston': { primary: '#000000', secondary: '#F2C94C' },
    // TODO(colors): verify — Launceston "Blues", navy/white.
    Launceston: { primary: '#0C2F6B', secondary: '#FFFFFF' },
    // TODO(colors): verify — Glenorchy, see SFL.
    Glenorchy: TAS_SOUTH_CLUBS.Glenorchy,
    // TODO(colors): verify — Clarence, see SFL.
    Clarence: TAS_SOUTH_CLUBS.Clarence,
    // TODO(colors): verify — Kingborough, see SFL.
    Kingborough: TAS_SOUTH_CLUBS.Kingborough,
  },
  nwfl: TAS_MAN_CLUBS,
  sfl: TAS_SOUTH_CLUBS,
}

/**
 * Look up a club's palette within one league. Matching is
 * case/whitespace-insensitive so raw feed naming drift stays harmless.
 * Unknown league, unknown club, or empty input → null.
 */
export function leagueColorFor(
  leagueKey: string | null | undefined,
  teamName: string | null | undefined,
): ClubColors | null {
  if (!leagueKey || !teamName) return null
  const palette = LEAGUE_COLORS[leagueKey]
  if (!palette) return null
  const direct = palette[teamName]
  if (direct) return direct
  const key = teamName.trim().toLowerCase()
  if (!key) return null
  for (const [name, colors] of Object.entries(palette)) {
    if (name.trim().toLowerCase() === key) return colors
  }
  return null
}
