/**
 * TEAM-IDENTITY (2026-09-30, user request): population of the
 * TEAM_IDENTITY map from the frozen `GET /api/teams` payload — the
 * missing piece between the ingestion-captured crests and the badge
 * chain (logoFor resolution order stays untouched).
 *
 * populateTeamIdentity is pure-testable: it merges into the module map
 * keyed by normalizeTeam (alias map included), maps the snake_case
 * payload keys to the camelCase entry shape, skips entries with no
 * identity fields at all (backend LEFT-JOIN nulls), and is idempotent.
 *
 * syncTeamIdentity is the one-shot guarded fetch: a module-scoped flag
 * keyed on the sport argument means repeated page visits never refetch
 * (and the unfiltered call is guarded separately from a filtered one),
 * and EVERY failure resolves `false` instead of throwing so the
 * initials-badge fallback survives an API outage.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  populateTeamIdentity,
  syncTeamIdentity,
  TEAM_IDENTITY,
  TEAM_IDENTITY_SYNCED,
  type TeamIdentityEntry,
} from '../../composables/useTeamIdentity'
import { useTeamLogos } from '../../composables/useTeamLogos'
import type { TeamIdentityPayload } from '../../composables/useApi'

/** Build a fully-typed payload entry with null identity by default. */
const entry = (
  overrides: Partial<TeamIdentityPayload> & { name: string },
): TeamIdentityPayload => ({
  abbreviation: null,
  logo_url: null,
  primary_color: null,
  secondary_color: null,
  ...overrides,
})

const clearIdentityMap = () => {
  for (const key of Object.keys(TEAM_IDENTITY)) {
    delete TEAM_IDENTITY[key]
  }
}

describe('populateTeamIdentity (merge into TEAM_IDENTITY)', () => {
  beforeEach(clearIdentityMap)

  it('keys entries via normalizeTeam (trim + alias map included)', () => {
    const { normalizeTeam } = useTeamLogos()
    populateTeamIdentity([
      entry({ name: 'Peel Thunder', logo_url: 'https://cdn.example/peel.png' }),
      entry({ name: 'Brisbane Lions', logo_url: 'https://cdn.example/lions.png' }),
    ])
    expect(normalizeTeam('Brisbane Lions')).toBe('Brisbane')
    expect(TEAM_IDENTITY['Peel Thunder']).toBeDefined()
    // Alias names land on the canonical key, so identityFor("Brisbane Lions")
    // resolves the entry through the same normalisation.
    expect(TEAM_IDENTITY['Brisbane']?.logoUrl).toBe('https://cdn.example/lions.png')
  })

  it('maps snake_case payload keys to the camelCase entry shape', () => {
    populateTeamIdentity([
      entry({
        name: 'East Fremantle',
        logo_url: 'https://cdn.example/ef.png',
        primary_color: '#004B8D',
        secondary_color: '#FFFFFF',
      }),
    ])
    expect(TEAM_IDENTITY['East Fremantle']).toEqual({
      logoUrl: 'https://cdn.example/ef.png',
      primaryColor: '#004B8D',
      secondaryColor: '#FFFFFF',
    } satisfies TeamIdentityEntry)
  })

  it('skips entries with no identity fields (backend LEFT-JOIN nulls)', () => {
    const count = populateTeamIdentity([entry({ name: 'Identity-less FC' })])
    expect(count).toBe(0)
    expect(TEAM_IDENTITY['Identity-less FC']).toBeUndefined()
  })

  it('keeps entries that carry only SOME identity fields', () => {
    const count = populateTeamIdentity([
      entry({ name: 'Colours Only FC', primary_color: '#123456' }),
    ])
    expect(count).toBe(1)
    expect(TEAM_IDENTITY['Colours Only FC']).toEqual({ primaryColor: '#123456' })
  })

  it('returns the count of entries populated', () => {
    const count = populateTeamIdentity([
      entry({ name: 'Peel Thunder', logo_url: 'https://cdn.example/peel.png' }),
      entry({ name: 'Identity-less FC' }),
      entry({ name: 'Perth', primary_color: '#000000' }),
    ])
    expect(count).toBe(2)
  })

  it('is idempotent: re-running the same payload leaves the map unchanged', () => {
    const payload = [
      entry({ name: 'Peel Thunder', logo_url: 'https://cdn.example/peel.png' }),
    ]
    const first = populateTeamIdentity(payload)
    const snapshot = { ...TEAM_IDENTITY }
    const second = populateTeamIdentity(payload)
    expect(first).toBe(1)
    expect(second).toBe(1)
    expect(TEAM_IDENTITY).toEqual(snapshot)
  })

  it('merges without wiping pre-existing entries', () => {
    TEAM_IDENTITY['Existing FC'] = { logoUrl: 'https://cdn.example/existing.png' }
    populateTeamIdentity([entry({ name: 'New FC', primary_color: '#ABCDEF' })])
    expect(TEAM_IDENTITY['Existing FC']?.logoUrl).toBe('https://cdn.example/existing.png')
    expect(TEAM_IDENTITY['New FC']).toBeDefined()
  })
})

describe('syncTeamIdentity (one-shot guarded fetch)', () => {
  beforeEach(() => {
    clearIdentityMap()
    TEAM_IDENTITY_SYNCED.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubGetTeams(mock: ReturnType<typeof vi.fn>) {
    // syncTeamIdentity consumes useApi through the Nuxt auto-import
    // (same runtime-global resolution the composables use), so stubbing
    // the global is enough — mirrors league-events-fetch.test.ts.
    vi.stubGlobal('useApi', () => ({ getTeams: mock }))
  }

  it('fetches via useApi.getTeams and populates the identity map', async () => {
    const getTeams = vi.fn(async () => ({
      teams: [entry({ name: 'Peel Thunder', logo_url: 'https://cdn.example/peel.png' })],
    }))
    stubGetTeams(getTeams)

    const ok = await syncTeamIdentity('afl')

    expect(ok).toBe(true)
    expect(TEAM_IDENTITY['Peel Thunder']?.logoUrl).toBe('https://cdn.example/peel.png')
    expect(getTeams).toHaveBeenCalledTimes(1)
    expect(getTeams).toHaveBeenCalledWith('afl')
  })

  it('the guard means a second call for the same sport does NOT refetch', async () => {
    const getTeams = vi.fn(async () => ({ teams: [] }))
    stubGetTeams(getTeams)

    await syncTeamIdentity('afl')
    const second = await syncTeamIdentity('afl')

    expect(getTeams).toHaveBeenCalledTimes(1)
    // Already-synced is a success, not an error.
    expect(second).toBe(true)
  })

  it('the unfiltered call and a filtered call are guarded SEPARATELY', async () => {
    const getTeams = vi.fn(async () => ({ teams: [] }))
    stubGetTeams(getTeams)

    await syncTeamIdentity()
    await syncTeamIdentity('afl')
    await syncTeamIdentity()

    expect(getTeams).toHaveBeenCalledTimes(2)
    expect(getTeams).toHaveBeenNthCalledWith(1, undefined)
    expect(getTeams).toHaveBeenNthCalledWith(2, 'afl')
  })

  it('resolves false on failure and NEVER throws (badge fallback survives an outage)', async () => {
    const getTeams = vi.fn(async () => {
      throw new Error('API down')
    })
    stubGetTeams(getTeams)

    await expect(syncTeamIdentity('afl')).resolves.toBe(false)
    expect(TEAM_IDENTITY['Peel Thunder']).toBeUndefined()
  })

  it('treats a missing teams array as nothing to populate (still a success)', async () => {
    const getTeams = vi.fn(async () => ({ no_teams_here: true }))
    stubGetTeams(getTeams)

    await expect(syncTeamIdentity('afl')).resolves.toBe(true)
    expect(Object.keys(TEAM_IDENTITY)).toHaveLength(0)
  })
})
