import { describe, it, expect, beforeEach } from 'vitest'
import {
  useTeamIdentity,
  TEAM_IDENTITY,
  type TeamIdentityEntry,
} from '../../composables/useTeamIdentity'
import { useTeamLogos } from '../../composables/useTeamLogos'
import { LEAGUE_COLORS } from '../../composables/useLeagueColors'

/**
 * Data-driven team identity (backend migration 0011): TEAM_IDENTITY
 * entries captured at ingestion override the hard-coded AFL maps;
 * everything else falls through to useTeamLogos/useTeamColors.
 * The map ships EMPTY — AFL teams resolve entirely via fallback.
 */

describe('useTeamIdentity fallbacks (empty TEAM_IDENTITY)', () => {
  it('falls back to the AFL logo map for known teams', () => {
    const { logoFor } = useTeamIdentity()
    expect(logoFor('West Coast')).toBe('/logos/WestCoast.png')
    expect(logoFor('Western Bulldogs')).toBe('/logos/Bulldogs.png')
  })

  it('normalises aliases before lookup', () => {
    const { logoFor } = useTeamIdentity()
    expect(logoFor('Brisbane Lions')).toBe('/logos/Brisbane.png')
  })

  it('falls back to the placeholder for unknown/missing teams', () => {
    const { logoFor } = useTeamIdentity()
    const { PLACEHOLDER_LOGO } = useTeamLogos()
    expect(logoFor('Mt Gravatt Vultures')).toBe(PLACEHOLDER_LOGO)
    expect(logoFor('TBD')).toBe(PLACEHOLDER_LOGO)
    expect(logoFor(null)).toBe(PLACEHOLDER_LOGO)
    expect(logoFor(undefined)).toBe(PLACEHOLDER_LOGO)
  })

  it('falls back to the AFL colour palette by index', () => {
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Richmond', 0)).toBe('#FFD700')
    expect(colorFor('Richmond', 1)).toBe('#000000')
  })

  it('colour fallback returns null past the palette length', () => {
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Carlton', 5)).toBeNull()
  })

  it('colour fallback covers null/unknown teams via the default palette', () => {
    const { colorFor } = useTeamIdentity()
    expect(colorFor(null, 0)).toBe('#FFD700')
    expect(colorFor('Totally Unknown FC', 0)).toBe('#FFD700')
  })
})

describe('useTeamIdentity explicit entries override', () => {
  beforeEach(() => {
    for (const key of Object.keys(TEAM_IDENTITY)) {
      delete TEAM_IDENTITY[key]
    }
  })

  it('logoFor prefers an explicit entry', () => {
    TEAM_IDENTITY['Mt Gravatt Vultures'] = {
      logoUrl: 'https://cdn.example/vultures.png',
    }
    const { logoFor } = useTeamIdentity()
    expect(logoFor('Mt Gravatt Vultures')).toBe(
      'https://cdn.example/vultures.png',
    )
  })

  it('an entry only overrides ITS OWN team', () => {
    TEAM_IDENTITY['Sherwood Magpies'] = {
      logoUrl: 'https://cdn.example/magpies.png',
    }
    const { logoFor } = useTeamIdentity()
    const { PLACEHOLDER_LOGO } = useTeamLogos()
    expect(logoFor('Sherwood Magpies')).toBe(
      'https://cdn.example/magpies.png',
    )
    expect(logoFor('Morningside Panthers')).toBe(PLACEHOLDER_LOGO)
  })

  it('entries without a logo still fall back for the logo', () => {
    TEAM_IDENTITY['Coorparoo Kings'] = { primaryColor: '#123456' }
    const { logoFor } = useTeamIdentity()
    expect(logoFor('Coorparoo Kings')).toBe(useTeamLogos().PLACEHOLDER_LOGO)
  })

  it('an entry keyed by canonical name catches its aliases', () => {
    TEAM_IDENTITY['Brisbane'] = { logoUrl: 'https://cdn.example/lions.png' }
    const { logoFor } = useTeamIdentity()
    expect(logoFor('Brisbane Lions')).toBe('https://cdn.example/lions.png')
  })

  it('colorFor uses primary/secondary from the entry', () => {
    const entry: TeamIdentityEntry = {
      primaryColor: '#002B5C',
      secondaryColor: '#E31937',
    }
    TEAM_IDENTITY['Aspley Hornets'] = entry
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Aspley Hornets', 0)).toBe('#002B5C')
    expect(colorFor('Aspley Hornets', 1)).toBe('#E31937')
    expect(colorFor('Aspley Hornets', 2)).toBeNull()
  })

  it('a primary-only entry leaves the secondary slot null (no AFL fallthrough)', () => {
    TEAM_IDENTITY['Noosa Tigers'] = { primaryColor: '#112233' }
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Noosa Tigers', 0)).toBe('#112233')
    expect(colorFor('Noosa Tigers', 1)).toBeNull()
  })

  it('a logo-only entry still falls back for colours', () => {
    TEAM_IDENTITY['Labrador Tigers'] = {
      logoUrl: 'https://cdn.example/tigers.png',
    }
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Labrador Tigers', 0)).toBe('#FFD700')
  })

  it('entries do not leak between tests (beforeEach clears the map)', () => {
    expect(TEAM_IDENTITY['Aspley Hornets']).toBeUndefined()
  })
})

describe('useTeamIdentity league-palette fallback', () => {
  beforeEach(() => {
    for (const key of Object.keys(TEAM_IDENTITY)) {
      delete TEAM_IDENTITY[key]
    }
  })

  it('resolves a state-league club through the league palette', () => {
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Peel Thunder', 0, 'wafl')).toBe(
      LEAGUE_COLORS.wafl['Peel Thunder'].primary,
    )
    expect(colorFor('Peel Thunder', 1, 'wafl')).toBe(
      LEAGUE_COLORS.wafl['Peel Thunder'].secondary,
    )
  })

  it('normalises case/whitespace for the league lookup', () => {
    const { colorFor } = useTeamIdentity()
    expect(colorFor('  claremont ', 0, 'wafl')).toBe(
      LEAGUE_COLORS.wafl['Claremont'].primary,
    )
  })

  it('returns null past the league palette length', () => {
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Claremont', 2, 'wafl')).toBeNull()
  })

  it('without a league key, behaviour is unchanged (AFL fallback)', () => {
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Claremont', 0)).toBe('#FFD700')
    expect(colorFor('Richmond', 0)).toBe('#FFD700')
  })

  it('a club missing from the league palette falls to the AFL palette', () => {
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Richmond', 0, 'wafl')).toBe('#FFD700')
    expect(colorFor('Richmond', 1, 'wafl')).toBe('#000000')
  })

  it('an unknown league key falls to the AFL palette', () => {
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Peel Thunder', 0, 'not-a-league')).toBe('#FFD700')
  })

  it('an explicit identity entry beats the league palette', () => {
    TEAM_IDENTITY['Peel Thunder'] = { primaryColor: '#123456' }
    const { colorFor } = useTeamIdentity()
    expect(colorFor('Peel Thunder', 0, 'wafl')).toBe('#123456')
    expect(colorFor('Peel Thunder', 1, 'wafl')).toBeNull()
  })
})
