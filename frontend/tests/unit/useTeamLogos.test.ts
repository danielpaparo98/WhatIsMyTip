import { describe, it, expect, beforeAll } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { useTeamLogos } from '../../composables/useTeamLogos'

// CR-005 from Phase 2b: cross-reference every key in TEAM_LOGOS against
// the actual files served from /public/logos.  The previous version
// used human-friendly keys ("Brisbane Lions", "Western Bulldogs", ...)
// but the backend normalises those to the canonical Squiggle names
// ("Brisbane", "Bulldogs", ...), so the logo lookup silently returned
// an empty string for 9 of the 18 teams.

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const LOGOS_DIR = resolve(__dirname, '..', '..', 'public', 'logos')
const SOURCE = readFileSync(
  resolve(__dirname, '..', '..', 'composables', 'useTeamLogos.ts'),
  'utf8',
)

// Parse `TEAM_LOGOS` from the source.  Matches both quoted keys
// (e.g. `'Brisbane': 'Brisbane.png'`) and unquoted ones
// (e.g. `Brisbane: 'Brisbane.png'`) so the test is robust to either
// style.  This is deliberately minimal — we only care about the
// literal entries in the dict, not its runtime use.  The match is
// ANCHORED to the `const TEAM_LOGOS` declaration so sibling maps
// (NRL_TEAM_LOGOS — rugby-league crests, .svg/.png) are not swept in.
const TEAM_LOGOS: Record<string, string> = (() => {
  const out: Record<string, string> = {}
  const decl = SOURCE.match(/const TEAM_LOGOS: Record<string, string> = \{([\s\S]*?)\n\}/)
  const body = decl?.[1] ?? ''
  const re = /(?:'([^']+)'|([A-Za-z][\w]*))\s*:\s*'([^']+)'/g
  let m: RegExpExecArray | null
  while ((m = re.exec(body)) !== null) {
    const key = m[1] ?? m[2]
    if (key && m[3]) out[key] = m[3]
  }
  return out
})()

describe('TEAM_LOGOS mapping', () => {
  beforeAll(() => {
    // Sanity: the test must be able to find at least one logo file.
    expect(existsSync(LOGOS_DIR)).toBe(true)
  })

  it('contains exactly 18 AFL teams', () => {
    // AFL has 18 clubs.  The mapping must not silently drop or duplicate.
    expect(Object.keys(TEAM_LOGOS).length).toBe(18)
  })

  describe('NRL_TEAM_LOGOS mapping (rugby-league crests)', () => {
    // Same source-parse approach, anchored to the NRL declaration.
    const NRL_TEAM_LOGOS: Record<string, string> = (() => {
      const out: Record<string, string> = {}
      const decl = SOURCE.match(
        /export const NRL_TEAM_LOGOS: Record<string, string> = \{([\s\S]*?)\n\}/,
      )
      const body = decl?.[1] ?? ''
      const re = /(?:'([^']+)'|([A-Za-z][\w ]*))\s*:\s*'([^']+)'/g
      let m: RegExpExecArray | null
      while ((m = re.exec(body)) !== null) {
        const key = m[1] ?? m[2]
        if (key && m[3]) out[key] = m[3]
      }
      return out
    })()

    it('contains exactly 19 rugby-league identities (17 clubs + Blues + Maroons)', () => {
      expect(Object.keys(NRL_TEAM_LOGOS).length).toBe(19)
    })

    it('every key maps to a crest file that actually exists in /public/logos/nrl', () => {
      const missing: string[] = []
      for (const [team, filename] of Object.entries(NRL_TEAM_LOGOS)) {
        if (!existsSync(resolve(LOGOS_DIR, filename))) {
          missing.push(`${team} -> ${filename}`)
        }
      }
      expect(missing, `Missing crest files: ${missing.join(', ')}`).toEqual([])
    })

    it('getLogoUrl resolves rugby-league names under a rugby-league key', () => {
      const { getLogoUrl } = useTeamLogos()
      expect(getLogoUrl('Broncos', 'nrl')).toBe('/logos/nrl/broncos.svg')
      expect(getLogoUrl('Wests Tigers', 'nrlw')).toBe('/logos/nrl/wests_tigers.svg')
      expect(getLogoUrl('Blues', 'origin')).toBe('/logos/nrl/blues.svg')
    })

    it('stays on the AFL map without a rugby-league league key', () => {
      const { getLogoUrl } = useTeamLogos()
      expect(getLogoUrl('Broncos')).not.toContain('/nrl/')
      expect(getLogoUrl('Brisbane', 'nrl')).toBe('/logos/Brisbane.png')
    })
  })

  it('every key maps to a file that actually exists in /public/logos', () => {
    const missing: string[] = []
    for (const [team, filename] of Object.entries(TEAM_LOGOS)) {
      if (!existsSync(resolve(LOGOS_DIR, filename))) {
        missing.push(`${team} -> ${filename}`)
      }
    }
    expect(missing, `Missing logo files: ${missing.join(', ')}`).toEqual([])
  })

  it('keys match the backend canonical team names (no "Brisbane Lions", "Western Bulldogs", etc.)', () => {
    // The backend's _canonical_team() normalises human-friendly aliases
    // (from Squiggle/AFL Tables) to the Squiggle canonical form.  The
    // mapping's keys must match what the backend actually returns, or
    // every logo lookup will silently fail.
    const expectedCanonicalNames = [
      'Adelaide',
      'Brisbane',
      'Bulldogs',
      'Carlton',
      'Collingwood',
      'Essendon',
      'Fremantle',
      'Geelong',
      'Giants',
      'GoldCoast',
      'Hawthorn',
      'Melbourne',
      'NorthMelbourne',
      'PortAdelaide',
      'Richmond',
      'StKilda',
      'Sydney',
      'WestCoast',
    ]
    for (const name of expectedCanonicalNames) {
      expect(TEAM_LOGOS, `Missing canonical name: ${name}`).toHaveProperty(name)
    }
  })

  it('does not include human-friendly aliases as keys', () => {
    // None of these should be present as keys — they would never match
    // what the backend sends.
    const forbiddenKeys = [
      'Brisbane Lions',
      'Western Bulldogs',
      'Gold Coast',
      'Greater Western Sydney',
      'North Melbourne',
      'Port Adelaide',
      'St Kilda',
      'West Coast',
    ]
    for (const k of forbiddenKeys) {
      expect(TEAM_LOGOS, `Unexpected alias as key: ${k}`).not.toHaveProperty(k)
    }
  })
})

describe('getLogoUrl alias resolution', () => {
  const { getLogoUrl, PLACEHOLDER_LOGO } = useTeamLogos()

  // These raw Squiggle forms previously rendered broken logos because
  // they did not match a TEAM_LOGOS key. After adding the alias
  // normaliser they must all resolve to a real /logos/*.png URL.
  const cases: Array<[string, string]> = [
    ['Western Bulldogs', '/logos/Bulldogs.png'],
    ['Footscray', '/logos/Bulldogs.png'],
    ['GWS', '/logos/Giants.png'],
    ['Greater Western Sydney', '/logos/Giants.png'],
    ['Gold Coast', '/logos/GoldCoast.png'],
    ['North Melbourne', '/logos/NorthMelbourne.png'],
    ['Port Adelaide', '/logos/PortAdelaide.png'],
    ['St Kilda', '/logos/StKilda.png'],
    ['West Coast', '/logos/WestCoast.png'],
    ['Brisbane Lions', '/logos/Brisbane.png'],
    ['Sydney Swans', '/logos/Sydney.png'],
  ]

  it.each(cases)('resolves alias "%s" to %s', (alias, expected) => {
    expect(getLogoUrl(alias)).toBe(expected)
  })

  it('resolves canonical names directly', () => {
    expect(getLogoUrl('Bulldogs')).toBe('/logos/Bulldogs.png')
    expect(getLogoUrl('Collingwood')).toBe('/logos/Collingwood.png')
  })

  // M-1 (2026-09 review): unknown/null teams now resolve to an inline
  // placeholder SVG — NEVER an empty string.  <img src=""> resolves to
  // the current document URL per spec, so the browser fetched the
  // page's own HTML as an image (broken icon + wasted request).
  describe('placeholder for unknown/missing teams (M-1)', () => {
    it('returns the placeholder for null/undefined/empty', () => {
      expect(getLogoUrl(null)).toBe(PLACEHOLDER_LOGO)
      expect(getLogoUrl(undefined)).toBe(PLACEHOLDER_LOGO)
      expect(getLogoUrl('')).toBe(PLACEHOLDER_LOGO)
    })

    it('returns the placeholder for an unknown team', () => {
      expect(getLogoUrl('Tasmania Devils')).toBe(PLACEHOLDER_LOGO)
    })

    it('placeholder is an inline data-URI SVG (no extra request)', () => {
      expect(PLACEHOLDER_LOGO).toMatch(/^data:image\/svg\+xml/)
    })

    it('placeholder is never an empty string (the old bug)', () => {
      expect(PLACEHOLDER_LOGO.length).toBeGreaterThan(0)
    })
  })
})
