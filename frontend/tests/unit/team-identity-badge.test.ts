import { describe, it, expect, beforeEach } from 'vitest'
import {
  useTeamIdentity,
  initialsBadge,
  initialsFor,
  TEAM_IDENTITY,
} from '../../composables/useTeamIdentity'
import { useTeamLogos } from '../../composables/useTeamLogos'
import {
  LEAGUE_COLORS,
  leagueColorFor,
  type ClubColors,
} from '../../composables/useLeagueColors'

/**
 * LEAGUE-ROUTES (2026-09-30, user request): clubs without a logo file
 * (every state-league side) get a generated inline SVG initials badge
 * instead of the neutral placeholder, when the caller can supply the
 * league key so leagueColorFor can resolve the club palette.
 *
 * The generator is a pure function: tests assert determinism, the
 * data-URI contract, and colour usage directly — no component mounting.
 */

const SVG_URI_PREFIX = 'data:image/svg+xml;utf8,'

const decodeSvg = (uri: string): string =>
  decodeURIComponent(uri.slice(SVG_URI_PREFIX.length))

describe('initialsFor (deterministic initials derivation)', () => {
  it('takes the first letter of the first two words', () => {
    expect(initialsFor('Peel Thunder')).toBe('PT')
    expect(initialsFor('East Fremantle')).toBe('EF')
  })

  it('caps at two letters for badge legibility at 40px', () => {
    expect(initialsFor('Box Hill Hawks')).toBe('BH')
    expect(initialsFor('Palm Beach Currumbin Lions')).toBe('PB')
  })

  it('handles single-word names with the first two characters', () => {
    expect(initialsFor('Perth')).toBe('PE')
    expect(initialsFor('Claremont')).toBe('CL')
    expect(initialsFor('Subiaco')).toBe('SU')
  })

  it('is case/whitespace-insensitive (feed-name drift stays harmless)', () => {
    expect(initialsFor('  peel   thunder ')).toBe('PT')
    expect(initialsFor('PEEL THUNDER')).toBe('PT')
  })

  it('keeps hyphenated words as one word', () => {
    expect(initialsFor('Woodville-West Torrens')).toBe('WT')
  })

  it('returns empty for empty/whitespace input', () => {
    expect(initialsFor('')).toBe('')
    expect(initialsFor('   ')).toBe('')
  })
})

describe('initialsBadge (pure inline SVG data URI)', () => {
  const peel: ClubColors = LEAGUE_COLORS.wafl['Peel Thunder'] // navy #000066 / white

  it('is deterministic: identical inputs yield the identical data URI', () => {
    expect(initialsBadge('Peel Thunder', peel)).toBe(
      initialsBadge('Peel Thunder', peel),
    )
    expect(initialsBadge('  peel thunder ', peel)).toBe(
      initialsBadge('Peel Thunder', peel),
    )
  })

  it('returns a data:image/svg+xml URI (PLACEHOLDER_LOGO contract)', () => {
    const badge = initialsBadge('Peel Thunder', peel)
    expect(badge).not.toBeNull()
    expect(badge).toMatch(/^data:image\/svg\+xml/)
    expect(badge?.startsWith(SVG_URI_PREFIX)).toBe(true)
  })

  it('encodes the club primary colour as the badge background', () => {
    const svg = decodeSvg(initialsBadge('Peel Thunder', peel)!)
    expect(svg).toContain('<svg xmlns="http://www.w3.org/2000/svg"')
    expect(svg).toContain('fill="#000066"')
  })

  it('renders the derived initials as text content', () => {
    expect(decodeSvg(initialsBadge('Peel Thunder', peel)!)).toContain('>PT<')
    expect(decodeSvg(initialsBadge('Perth', LEAGUE_COLORS.wafl['Perth'])!)).toContain(
      '>PE<',
    )
  })

  it('prefers the club secondary for the text when it contrasts with the primary', () => {
    // Peel Thunder: white secondary on a dark navy primary.
    const svg = decodeSvg(initialsBadge('Peel Thunder', peel)!)
    expect(svg).toContain('fill="#FFFFFF"')
  })

  it('falls back to a contrasting neutral when the secondary is too close to the primary', () => {
    const lowContrast: ClubColors = { primary: '#333333', secondary: '#444444' }
    const svg = decodeSvg(initialsBadge('Perth', lowContrast)!)
    expect(svg).toContain('fill="#FFFFFF"')
  })

  it('uses dark text on a light primary (Werribee gold)', () => {
    const werribee = LEAGUE_COLORS.vfl['Werribee'] // gold #FED102 / black
    const svg = decodeSvg(initialsBadge('Werribee', werribee)!)
    expect(svg).toContain('fill="#000000"')
  })

  it('returns null for missing names or missing palettes', () => {
    expect(initialsBadge(null, peel)).toBeNull()
    expect(initialsBadge(undefined, peel)).toBeNull()
    expect(initialsBadge('', peel)).toBeNull()
    expect(initialsBadge('Peel Thunder', null)).toBeNull()
  })

  it('accepts the leagueColorFor lookup result directly (incl. null for unknown clubs)', () => {
    expect(initialsBadge('Peel Thunder', leagueColorFor('wafl', 'Peel Thunder'))).toBe(
      initialsBadge('Peel Thunder', peel),
    )
    expect(
      initialsBadge('Richmond', leagueColorFor('wafl', 'Richmond')),
    ).toBeNull()
  })
})

describe('logoFor league-aware initials-badge fallback', () => {
  beforeEach(() => {
    for (const key of Object.keys(TEAM_IDENTITY)) {
      delete TEAM_IDENTITY[key]
    }
  })

  it('without a league key, behaviour is unchanged (placeholder for unknown clubs)', () => {
    const { logoFor } = useTeamIdentity()
    const { PLACEHOLDER_LOGO } = useTeamLogos()
    expect(logoFor('Peel Thunder')).toBe(PLACEHOLDER_LOGO)
    expect(logoFor('Mt Gravatt Vultures')).toBe(PLACEHOLDER_LOGO)
  })

  it('without a league key, AFL clubs still resolve to their logo files', () => {
    const { logoFor } = useTeamIdentity()
    expect(logoFor('West Coast')).toBe('/logos/WestCoast.png')
    expect(logoFor('Western Bulldogs')).toBe('/logos/Bulldogs.png')
  })

  it('resolves a state-league club to an initials badge when the league key is passed', () => {
    const { logoFor } = useTeamIdentity()
    const badge = logoFor('Peel Thunder', 'wafl')
    expect(badge).not.toBeNull()
    expect(badge).toMatch(/^data:image\/svg\+xml/)
    expect(decodeSvg(badge!)).toContain('>PT<')
    expect(decodeSvg(badge!)).toContain(
      `fill="${LEAGUE_COLORS.wafl['Peel Thunder'].primary}"`,
    )
  })

  it('is deterministic through logoFor too', () => {
    const { logoFor } = useTeamIdentity()
    expect(logoFor('Peel Thunder', 'wafl')).toBe(logoFor('Peel Thunder', 'wafl'))
  })

  it('a real AFL logo file still wins over a generated badge', () => {
    const { logoFor } = useTeamIdentity()
    // Collingwood is in the VFL palette AND has a real logo file.
    expect(logoFor('Collingwood', 'vfl')).toBe('/logos/Collingwood.png')
    // West Coast reserves appear in WAFL — alias normalisation still resolves the file.
    expect(logoFor('West Coast', 'wafl')).toBe('/logos/WestCoast.png')
  })

  it('a club missing from the league palette keeps its AFL file, or the placeholder when nothing exists', () => {
    const { logoFor } = useTeamIdentity()
    const { PLACEHOLDER_LOGO } = useTeamLogos()
    // Richmond has a real AFL logo file (and no WAFL palette entry) — the file wins.
    expect(logoFor('Richmond', 'wafl')).toBe('/logos/Richmond.png')
    // A club known to neither map falls to the neutral placeholder.
    expect(logoFor('Tasmania Devils', 'wafl')).toBe(PLACEHOLDER_LOGO)
  })

  it('an unknown league key falls to the placeholder', () => {
    const { logoFor } = useTeamIdentity()
    const { PLACEHOLDER_LOGO } = useTeamLogos()
    expect(logoFor('Peel Thunder', 'not-a-league')).toBe(PLACEHOLDER_LOGO)
  })

  it('null/undefined teams keep the placeholder even with a league key', () => {
    const { logoFor } = useTeamIdentity()
    const { PLACEHOLDER_LOGO } = useTeamLogos()
    expect(logoFor(null, 'wafl')).toBe(PLACEHOLDER_LOGO)
    expect(logoFor(undefined, 'wafl')).toBe(PLACEHOLDER_LOGO)
    expect(logoFor('TBD', 'wafl')).toBe(PLACEHOLDER_LOGO)
  })

  it('an explicit identity entry with a logo still beats the generated badge', () => {
    TEAM_IDENTITY['Peel Thunder'] = {
      logoUrl: 'https://cdn.example/thunder.png',
    }
    const { logoFor } = useTeamIdentity()
    expect(logoFor('Peel Thunder', 'wafl')).toBe(
      'https://cdn.example/thunder.png',
    )
  })
})
