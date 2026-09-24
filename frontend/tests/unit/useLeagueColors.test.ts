import { describe, it, expect } from 'vitest'
import {
  LEAGUE_COLORS,
  leagueColorFor,
  type ClubColors,
} from '../../composables/useLeagueColors'

/**
 * Hand-curated per-club brand palettes for the non-AFL leagues
 * (backend state-league rollout). Entries are best-effort curation;
 * uncertain colours carry a TODO(colors) comment at the source.
 */

const HEX_RE = /^#[0-9A-Fa-f]{6}$/

const WAFL_CLUBS = [
  'Claremont',
  'East Fremantle',
  'East Perth',
  'Peel Thunder',
  'Perth',
  'South Fremantle',
  'Subiaco',
  'Swan Districts',
  'West Perth',
  'West Coast',
]

const VFL_CLUBS = [
  'Box Hill Hawks',
  'Casey Demons',
  'Collingwood',
  'Essendon',
  'Frankston',
  'Geelong',
  'North Melbourne',
  'Port Melbourne',
  'Richmond',
  'Sandringham',
  'Southport',
  'Sydney Swans',
  'Werribee',
  'Williamstown',
  'Coburg',
  'Brisbane Lions',
  'Carlton',
  'Northern Bullants',
]

const SANFL_CLUBS = [
  'Adelaide',
  'Glenelg',
  'North Adelaide',
  'Norwood',
  'Port Adelaide Magpies',
  'South Adelaide',
  'Sturt',
  'West Adelaide',
  'Woodville-West Torrens',
  'Central District',
  'Eagles',
]

const QAFL_CLUBS = [
  'Aspley Hornets',
  'Broadbeach Cats',
  'Coorparoo Kings',
  'Gold Coast Suns',
  'Brisbane Lions',
  'Labrador Tigers',
  'Maroochydore',
  'Morningside Panthers',
  'Moreton Bay Lions',
  'Mount Gravatt Vultures',
  'Noosa Tigers',
  'Palm Beach Currumbin Lions',
  'Redland-Victoria Point Sharks',
  'Sherwood Magpies',
  'Southport',
  'Surfers Paradise Demons',
  'UQ Red Lions',
  'Wilston Grange Gorillas',
]

function expectClubPalette(club: ClubColors | undefined): void {
  expect(club).toBeDefined()
  expect(club!.primary).toMatch(HEX_RE)
  expect(club!.secondary).toMatch(HEX_RE)
}

describe('LEAGUE_COLORS coverage', () => {
  it('covers every non-AFL state-league key', () => {
    expect(Object.keys(LEAGUE_COLORS).sort()).toEqual(
      [
        'aflw',
        'nwfl',
        'qafl',
        'qaflw',
        'sanfl',
        'sfl',
        'tsl',
        'vfl',
        'vflw',
        'wafl',
        'waflw',
      ].sort(),
    )
  })

  it('every entry is a hex primary/secondary pair', () => {
    for (const [league, palette] of Object.entries(LEAGUE_COLORS)) {
      expect(Object.keys(palette).length, league).toBeGreaterThan(0)
      for (const [club, colors] of Object.entries(palette)) {
        expectClubPalette(colors)
        void club
      }
    }
  })

  it('covers the WAFL clubs', () => {
    for (const club of WAFL_CLUBS) {
      expectClubPalette(LEAGUE_COLORS.wafl[club])
    }
  })

  it('waflw shares the WAFL club map', () => {
    expect(LEAGUE_COLORS.waflw).toBe(LEAGUE_COLORS.wafl)
  })

  it('covers the VFL clubs', () => {
    for (const club of VFL_CLUBS) {
      expectClubPalette(LEAGUE_COLORS.vfl[club])
    }
  })

  it('vflw includes the shared VFL clubs', () => {
    expect(LEAGUE_COLORS.vflw['Box Hill Hawks']).toEqual(
      LEAGUE_COLORS.vfl['Box Hill Hawks'],
    )
    expectClubPalette(LEAGUE_COLORS.vflw['Darebin Falcons'])
  })

  it('covers the SANFL clubs incl. both Eagles spellings', () => {
    for (const club of SANFL_CLUBS) {
      expectClubPalette(LEAGUE_COLORS.sanfl[club])
    }
    expect(LEAGUE_COLORS.sanfl['Eagles']).toEqual(
      LEAGUE_COLORS.sanfl['Woodville-West Torrens'],
    )
  })

  it('covers the QAFL clubs, shared with QAFLW', () => {
    for (const club of QAFL_CLUBS) {
      expectClubPalette(LEAGUE_COLORS.qafl[club])
    }
    expect(LEAGUE_COLORS.qaflw).toBe(LEAGUE_COLORS.qafl)
  })

  it('offers best-effort Tasmanian entries for tsl/nwfl/sfl', () => {
    expect(Object.keys(LEAGUE_COLORS.tsl).length).toBeGreaterThan(0)
    expect(Object.keys(LEAGUE_COLORS.nwfl).length).toBeGreaterThan(0)
    expect(Object.keys(LEAGUE_COLORS.sfl).length).toBeGreaterThan(0)
    expectClubPalette(LEAGUE_COLORS.nwfl['Devonport'])
    expectClubPalette(LEAGUE_COLORS.sfl['Glenorchy'])
  })
})

describe('leagueColorFor', () => {
  it('resolves an exact club name', () => {
    expect(leagueColorFor('wafl', 'Peel Thunder')).toEqual(
      LEAGUE_COLORS.wafl['Peel Thunder'],
    )
  })

  it('resolves case/whitespace-insensitively', () => {
    expect(leagueColorFor('wafl', '  peel thunder ')).toEqual(
      LEAGUE_COLORS.wafl['Peel Thunder'],
    )
  })

  it('returns null for unknown league keys, missing names, or empty input', () => {
    expect(leagueColorFor(null, 'Peel Thunder')).toBeNull()
    expect(leagueColorFor('afl', 'Richmond')).toBeNull()
    expect(leagueColorFor('wafl', 'Richmond')).toBeNull()
    expect(leagueColorFor('wafl', null)).toBeNull()
  })
})
