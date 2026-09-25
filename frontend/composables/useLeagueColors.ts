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
// feed. Entries were verified in Sep 2026 against Wikipedia club articles
// (infobox colours/kits) and official club sites; the few still carrying
// a TODO(colors) comment resisted sourcing and are best effort.
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
  // Wikipedia infobox: navy #0C2340 / gold #FFC62B.
  Claremont: { primary: '#0C2340', secondary: '#FFC62B' },
  'East Fremantle': { primary: '#003DA5', secondary: '#FFFFFF' },
  // Wikipedia infobox: blue #003D7B, black (colours "Blue, Black").
  'East Perth': { primary: '#003D7B', secondary: '#000000' },
  // Wikipedia infobox: dark blue #000066, white, teal accent.
  'Peel Thunder': { primary: '#000066', secondary: '#FFFFFF' },
  // Wikipedia infobox: black, red #DE132E (black kit body).
  Perth: { primary: '#000000', secondary: '#DE132E' },
  'South Fremantle': { primary: '#D71920', secondary: '#FFFFFF' },
  // Wikipedia infobox: maroon #860038 / gold #FFC423.
  Subiaco: { primary: '#860038', secondary: '#FFC423' },
  'Swan Districts': { primary: '#000000', secondary: '#FFFFFF' },
  // Wikipedia: "cardinal and navy"; red hex #B50000, navy shade approximate.
  'West Perth': { primary: '#B50000', secondary: '#002B5C' },
  // West Coast reserves — parent-club palette.
  'West Coast': afl('WestCoast'),
}

const VFL_CLUBS: LeaguePalette = {
  'Box Hill Hawks': afl('Hawthorn'),
  // Melbourne's aligned reserves side — parent-club palette.
  'Casey Demons': afl('Melbourne'),
  Collingwood: afl('Collingwood'),
  Essendon: afl('Essendon'),
  // Wikipedia infobox: black, white, red (black kit body).
  Frankston: { primary: '#000000', secondary: '#FFFFFF' },
  Geelong: afl('Geelong'),
  'North Melbourne': afl('NorthMelbourne'),
  // Wikipedia infobox: blue #253A75, red (blue kit with red stripes).
  'Port Melbourne': { primary: '#253A75', secondary: '#D71920' },
  Richmond: afl('Richmond'),
  // Wikipedia: black, gold #FED102, blue (documented since 1929).
  Sandringham: { primary: '#000000', secondary: '#FED102' },
  // TODO(colors): unverified — best effort
  Southport: { primary: '#0C2F6B', secondary: '#F2C94C' },
  'Sydney Swans': afl('Sydney'),
  // Wikipedia infobox: black, gold #FED102 (gold tiger kit).
  Werribee: { primary: '#FED102', secondary: '#000000' },
  // Wikipedia infobox: blue #224B8E / gold #F5C751.
  Williamstown: { primary: '#224B8E', secondary: '#F5C751' },
  // Wikipedia: "retained navy blue and red as its main colours".
  Coburg: { primary: '#002B5C', secondary: '#D71920' },
  'Brisbane Lions': afl('Brisbane'),
  Carlton: afl('Carlton'),
  // Carlton's aligned reserves side — parent-club palette.
  'Northern Bullants': afl('Carlton'),
}

// VFLW shares most VFL clubs plus women's-only sides.
const VFLW_CLUBS: LeaguePalette = {
  ...VFL_CLUBS,
  // TODO(colors): unverified — best effort (green/white; no source found)
  'Darebin Falcons': { primary: '#1B6B45', secondary: '#FFFFFF' },
  // Wikipedia infobox: red #ED0F05, white, black (St Kilda-aligned).
  'Southern Saints': { primary: '#ED0F05', secondary: '#FFFFFF' },
}

const SANFL_CLUBS: LeaguePalette = {
  Adelaide: afl('Adelaide'),
  // Wikipedia infobox: black, gold #FEBC03 (black kit body).
  Glenelg: { primary: '#000000', secondary: '#FEBC03' },
  'North Adelaide': { primary: '#D71920', secondary: '#FFFFFF' },
  // Wikipedia infobox: navy #001448, red #EE0F49 (navy kit body).
  Norwood: { primary: '#001448', secondary: '#EE0F49' },
  'Port Adelaide Magpies': { primary: '#000000', secondary: '#FFFFFF' },
  // Wikipedia infobox: navy #021637, white.
  'South Adelaide': { primary: '#021637', secondary: '#FFFFFF' },
  // "Double Blues" — royal over navy.
  Sturt: { primary: '#2B4C97', secondary: '#0C2F6B' },
  'West Adelaide': { primary: '#C8102E', secondary: '#000000' },
  // "Eagles" — navy/gold; AFL platform uses both spellings.
  'Woodville-West Torrens': { primary: '#003DA5', secondary: '#F2C94C' },
  Eagles: { primary: '#003DA5', secondary: '#F2C94C' },
  // Wikipedia kit: blue #072E9D body with red #B50000.
  'Central District': { primary: '#072E9D', secondary: '#B50000' },
}

const QAFL_CLUBS: LeaguePalette = {
  // Wikipedia infobox: brown #814102 / gold #FFCC00 (hornet colours).
  'Aspley Hornets': { primary: '#814102', secondary: '#FFCC00' },
  // Wikipedia infobox: navy #1C3C63, white.
  'Broadbeach Cats': { primary: '#1C3C63', secondary: '#FFFFFF' },
  // Official club site theme: navy #0A2240, white.
  'Coorparoo Kings': { primary: '#0A2240', secondary: '#FFFFFF' },
  'Gold Coast Suns': afl('GoldCoast'),
  'Brisbane Lions': afl('Brisbane'),
  // Wikipedia infobox: black / gold #FFCC00.
  'Labrador Tigers': { primary: '#000000', secondary: '#FFCC00' },
  // Wikipedia infobox: green #006600 / gold #FFCC00.
  Maroochydore: { primary: '#006600', secondary: '#FFCC00' },
  // TODO(colors): unverified — best effort (black/white; no source found)
  'Morningside Panthers': { primary: '#000000', secondary: '#FFFFFF' },
  // TODO(colors): unverified — best effort (maroon/gold; no source found)
  'Moreton Bay Lions': { primary: '#6E1E3C', secondary: '#F2C94C' },
  // TODO(colors): unverified — best effort (maroon/white; no source found)
  'Mount Gravatt Vultures': { primary: '#6E1E3C', secondary: '#FFFFFF' },
  // Official club site theme: red #DF1717, navy #0B1D3B, amber accent.
  'Noosa Tigers': { primary: '#DF1717', secondary: '#0B1D3B' },
  // Wikipedia infobox: maroon #800000 / gold #FFCC00 (blue accent).
  'Palm Beach Currumbin Lions': { primary: '#800000', secondary: '#FFCC00' },
  // TODO(colors): unverified — best effort (navy/red; no source found)
  'Redland-Victoria Point Sharks': { primary: '#0C2F6B', secondary: '#D71920' },
  // TODO(colors): unverified — best effort (black/white; no source found)
  'Sherwood Magpies': { primary: '#000000', secondary: '#FFFFFF' },
  Southport: VFL_CLUBS.Southport,
  // Wikipedia infobox: navy #000066, red #FF0000.
  'Surfers Paradise Demons': { primary: '#000066', secondary: '#FF0000' },
  // Wikipedia infobox: maroon #8A0D2B, blue #035BA9.
  'UQ Red Lions': { primary: '#8A0D2B', secondary: '#035BA9' },
  // TODO(colors): unverified — best effort (black/white; no source found)
  'Wilston Grange Gorillas': { primary: '#000000', secondary: '#FFFFFF' },
}

// Tasmania — TSL/legacy regional leagues. Verified against Wikipedia
// club articles (infobox colours) where they exist; a couple of small
// clubs resist sourcing and stay best-effort.
const TAS_MAN_CLUBS: LeaguePalette = {
  // Wikipedia infobox: black, white (teal accent).
  Devonport: { primary: '#000000', secondary: '#FFFFFF' },
  // Wikipedia infobox: red #CC2031, black ("Robins").
  Ulverstone: { primary: '#CC2031', secondary: '#000000' },
  // Wikipedia infobox: navy #003399 / sky #66CCFF ("Two Blues").
  Penguin: { primary: '#003399', secondary: '#66CCFF' },
  // Wikipedia infobox: black, white.
  Wynyard: { primary: '#000000', secondary: '#FFFFFF' },
  // Wikipedia infobox: navy #000066, red #FF0000 ("Demons").
  Latrobe: { primary: '#000066', secondary: '#FF0000' },
  // Wikipedia infobox: red #E1251B, white ("Swans").
  'East Devonport': { primary: '#E1251B', secondary: '#FFFFFF' },
  // TODO(colors): unverified — best effort (blue/red; no source found)
  Smithton: { primary: '#2B4C97', secondary: '#D71920' },
}

const TAS_SOUTH_CLUBS: LeaguePalette = {
  // Wikipedia infobox: red #B50000, white ("Roos").
  Clarence: { primary: '#B50000', secondary: '#FFFFFF' },
  // Wikipedia infobox: black, white ("Magpies").
  Glenorchy: { primary: '#000000', secondary: '#FFFFFF' },
  // Wikipedia infobox: black / gold #FFCC00 ("Tigers").
  Kingborough: { primary: '#000000', secondary: '#FFCC00' },
  // Wikipedia infobox: blue #000066, red #FF0000 ("Demons").
  'North Hobart': { primary: '#000066', secondary: '#FF0000' },
  // Wikipedia: red/black "Bombers" (black kit body; formerly blue/white Cats).
  Lauderdale: { primary: '#000000', secondary: '#FF0000' },
  // TODO(colors): unverified — best effort (blue/white; no source found)
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
    // Wikipedia infobox: black, red #FF0000 ("Northern Bombers").
    'North Launceston': { primary: '#000000', secondary: '#FF0000' },
    // Wikipedia infobox: navy #050F40, white ("Blues").
    Launceston: { primary: '#050F40', secondary: '#FFFFFF' },
    Glenorchy: TAS_SOUTH_CLUBS.Glenorchy,
    Clarence: TAS_SOUTH_CLUBS.Clarence,
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
