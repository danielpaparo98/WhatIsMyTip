/**
 * Grand-final uplift (2026-09): home-page three-state switching.
 *
 *  1. grand_final — GF week (`is_grand_final && !is_post_season`):
 *     home page becomes the pre-match grand-final report.
 *  2. post_season — GF round fully completed: off-season celebration.
 *  3. regular — every other round: the original home page, unchanged.
 *
 * The gating logic is a pure exported function (`resolveHomeState`) and
 * is exercised directly.  Component/page wiring is asserted via
 * source-grep, matching the repo's established static-analysis test
 * style (the vitest config has no Vue plugin, so SFCs cannot be
 * mounted here — see aria-labels.test.ts / lazy-images.test.ts).
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolveHomeState, pickGrandFinalGame } from '~/composables/useLatestRound'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const INDEX = readFileSync(resolve(FRONTEND_ROOT, 'pages/index.vue'), 'utf8')
const LAYOUT = readFileSync(resolve(FRONTEND_ROOT, 'layouts/default.vue'), 'utf8')
const GF_REPORT = readFileSync(resolve(FRONTEND_ROOT, 'components/GrandFinalReport.vue'), 'utf8')
const CELEBRATION = readFileSync(resolve(FRONTEND_ROOT, 'components/OffSeasonCelebration.vue'), 'utf8')
const CONFETTI = readFileSync(resolve(FRONTEND_ROOT, 'components/ConfettiEffect.vue'), 'utf8')
const BANNER = readFileSync(resolve(FRONTEND_ROOT, 'components/OffSeasonBanner.vue'), 'utf8')
const USE_API = readFileSync(resolve(FRONTEND_ROOT, 'composables/useApi.ts'), 'utf8')

// ---------------------------------------------------------------------------
// State gating — behavioural, via the pure helper
// ---------------------------------------------------------------------------
describe('home state gating (resolveHomeState)', () => {
  it('returns regular for a missing round', () => {
    expect(resolveHomeState(null)).toBe('regular')
    expect(resolveHomeState(undefined)).toBe('regular')
  })

  it('returns regular when neither flag is set', () => {
    expect(resolveHomeState({ is_grand_final: false, is_post_season: false })).toBe('regular')
  })

  it('returns grand_final only when is_grand_final && !is_post_season', () => {
    expect(resolveHomeState({ is_grand_final: true, is_post_season: false })).toBe('grand_final')
  })

  it('post_season wins once the grand final has been played', () => {
    expect(resolveHomeState({ is_grand_final: true, is_post_season: true })).toBe('post_season')
    expect(resolveHomeState({ is_grand_final: false, is_post_season: true })).toBe('post_season')
  })
})

// ---------------------------------------------------------------------------
// useApi contract for the uplift
// ---------------------------------------------------------------------------
describe('useApi grand-final additions', () => {
  it('LatestRoundResponse declares is_post_season', () => {
    expect(USE_API).toMatch(/interface LatestRoundResponse[\s\S]*?is_post_season:\s*boolean/)
  })

  it('declares the report interfaces with backend field names', () => {
    expect(USE_API).toMatch(/export interface GrandFinalReport \{/)
    expect(USE_API).toMatch(/export interface MatchReportResponse \{/)
    expect(USE_API).toMatch(/export interface TeamStory \{/)
    expect(USE_API).toMatch(/export interface PlayerSpotlight \{/)
    expect(USE_API).toMatch(/export interface InjuryNote \{/)
    expect(USE_API).toMatch(/export interface ModelConsensus \{/)
    expect(USE_API).toMatch(/export interface Prediction \{/)
  })

  it('getGameReport is declared to return MatchReportResponse | null', () => {
    expect(USE_API).toMatch(
      /const getGameReport = async \(slug: string\): Promise<MatchReportResponse \| null>/,
    )
  })
})

// ---------------------------------------------------------------------------
// index.vue wiring — source-grep
// ---------------------------------------------------------------------------
describe('index.vue state wiring', () => {
  it('derives the state from resolveHomeState(round)', () => {
    expect(INDEX).toMatch(/resolveHomeState\(round\.value\)/)
    expect(INDEX).toMatch(/homeState\.value === 'grand_final'/)
    expect(INDEX).toMatch(/homeState\.value === 'post_season'/)
  })

  it('fetches the grand-final view with key / watch / dedupe contract', () => {
    expect(INDEX).toContain("'grand-final-view'")
    expect(INDEX).toMatch(/watch:\s*\[seasonRound\]/)
    expect(INDEX).toMatch(/dedupe:\s*'cancel'/)
  })

  it('resolves the round game then loads detail + report in parallel', () => {
    expect(INDEX).toMatch(/\.getGames\(\{/)
    expect(INDEX).toMatch(/Promise\.all\(\[/)
    expect(INDEX).toMatch(/api\.getGameDetail\(game\.slug\)\.catch\(\(\) => null\)/)
    expect(INDEX).toMatch(/api\.getGameReport\(game\.slug\)\.catch\(\(\) => null\)/)
  })

  it('GF-VIEW FIX: resolves the game via pickGrandFinalGame, never .find on the raw payload', () => {
    // PROD INCIDENT: getGames returns { games, count } — calling .find
    // on it threw on every load and baked the "check back soon" page.
    expect(INDEX).toMatch(/pickGrandFinalGame\(gamesPayload\)/)
    expect(INDEX).not.toMatch(/as Game\[\]/)
    expect(INDEX).toMatch(/api\s*\n\s*\.getGames\(\{[\s\S]*?\}\)\s*\n?\s*\.catch\(\(\) => null\)/)
  })

  it('GF-VIEW FIX: refreshes the grand-final view immediately on mount', () => {
    // The static bake can lag the backend; one client-side refetch on
    // load makes the page current without waiting for the 5-min poll.
    expect(INDEX).toMatch(/if \(isGrandFinal\.value\) \{[\s\S]*?refreshGrandFinal\(\)/)
  })

  // ---------------------------------------------------------------------
  // GF-DESIGN (2026-09-20, user direction)
  // ---------------------------------------------------------------------
  it('keeps the standard hero in the grand-final branch', () => {
    // The GF branch renders the same hero the regular weeks use, with
    // the report flowing below it.
    expect(INDEX).toMatch(
      /v-if="isGrandFinal"[\s\S]*?<section class="hero">[\s\S]*?<\/section>[\s\S]*?grandFinalPending/
    )
  })

  it('confetti is home-page only and uses both teams\u2019 colours', () => {
    // Mounted from index.vue with the two finalists' palettes...
    expect(INDEX).toMatch(/<ConfettiEffect :colors="gfConfettiColors" \/>/)
    expect(INDEX).toMatch(/getTeamColors\(game\.home_team\)/)
    expect(INDEX).toMatch(/getTeamColors\(game\.away_team\)/)
    // ...never from the site-wide layout.
    expect(LAYOUT).not.toContain('<ConfettiEffect')
  })

  it('OffSeasonBanner no longer fires confetti', () => {
    expect(BANNER).not.toContain('canvas-confetti')
    expect(BANNER).not.toContain('fireCelebration')
  })

  it('heuristic tips are passed into the report', () => {
    expect(INDEX).toMatch(/:tips="grandFinalDetail\?\.tips \?\? \[\]"/)
    expect(GF_REPORT).toContain('The Tips')
    expect(GF_REPORT).toMatch(/formatHeuristic\(tip\.heuristic\)/)
  })

  describe('pickGrandFinalGame (behavioural)', () => {
    const valid = { slug: 'xo9e0w3dnv', home_team: 'Fremantle', away_team: 'Brisbane' }
    const tbc = { slug: 'tbc12345', home_team: null, away_team: null }

    it('handles the GameListResponse envelope ({ games, count })', () => {
      expect(pickGrandFinalGame({ games: [tbc, valid], count: 2 })).toEqual(valid)
    })

    it('handles a bare array (defensive)', () => {
      expect(pickGrandFinalGame([tbc, valid])).toEqual(valid)
    })

    it('returns null for null/undefined/missing games', () => {
      expect(pickGrandFinalGame(null)).toBeNull()
      expect(pickGrandFinalGame(undefined)).toBeNull()
      expect(pickGrandFinalGame({})).toBeNull()
      expect(pickGrandFinalGame({ games: [] })).toBeNull()
    })

    it('skips TBC rows and returns null when no game has both teams', () => {
      expect(pickGrandFinalGame({ games: [tbc], count: 1 })).toBeNull()
    })
  })

  it('GF-TRIGGER: a report fetch failure degrades to null, never blanks the page', () => {
    // A 500 on /report must not reject the whole Promise.all and null
    // the detail view too (the "check back soon" incident).
    expect(INDEX).toMatch(/api\.getGameDetail\(game\.slug\)\.catch\(\(\) => null\)/)
    expect(INDEX).toMatch(/api\.getGameReport\(game\.slug\)\.catch\(\(\) => null\)/)
  })

  it('GF-TRIGGER: the poller adopts the report once it lands', () => {
    expect(INDEX).toMatch(
      /if \(isGrandFinal\.value && !grandFinalReport\.value\) \{[\s\S]*?await refreshGrandFinal\(\)/
    )
  })

  it('renders GrandFinalReport when the report is present', () => {
    expect(INDEX).toContain('<GrandFinalReport')
    // GF-VIEW FIX: pass the INNER report, not the MatchReportResponse
    // envelope (the envelope rendered an empty skeleton in prod).
    expect(INDEX).toContain(':report="grandFinalReport.report"')
    expect(INDEX).toContain(':game="grandFinalGame"')
  })

  it('falls back to the detail experience when the report 404s', () => {
    expect(INDEX).toContain('v-else-if="grandFinalDetail"')
    expect(INDEX).toContain('<WeatherCard')
    expect(INDEX).toContain('<TipCard')
    expect(INDEX).toContain('<MatchAnalysisCard')
  })

  it('renders OffSeasonCelebration with premier + season post-season', () => {
    expect(INDEX).toContain('<OffSeasonCelebration')
    expect(INDEX).toContain(':premier="round?.premier ?? null"')
    expect(INDEX).toContain(':season="round?.season ?? null"')
  })

  it('renders the heuristic tabs/games grid only in the regular branch', () => {
    const regularBranch = INDEX.indexOf('<template v-else>')
    expect(regularBranch).toBeGreaterThan(-1)
    // GF + post-season branches come BEFORE the regular branch, and the
    // regular content (hero/tabs/grid) lives inside it.
    expect(INDEX.indexOf('<GrandFinalReport')).toBeLessThan(regularBranch)
    expect(INDEX.indexOf('<OffSeasonCelebration')).toBeLessThan(regularBranch)
    expect(INDEX.indexOf('heuristic-selector')).toBeGreaterThan(regularBranch)
    expect(INDEX.indexOf('games-grid')).toBeGreaterThan(regularBranch)
  })

  it('keeps the heuristic restore and the single auto-refresh poller', () => {
    expect(INDEX).toMatch(/localStorage\.getItem\('selected-heuristic'\)/)
    expect(INDEX).toMatch(/AUTO_REFRESH_MS/)
    expect(INDEX).toMatch(/setLatestRound\(/)
  })

  it('keeps the off-season banner share of the latest-round store seeded', () => {
    expect(INDEX).toMatch(/setLatestRound\(round\.value\)/)
  })
})

// ---------------------------------------------------------------------------
// index.vue per-state SEO — source-grep
// ---------------------------------------------------------------------------
describe('index.vue per-state SEO', () => {
  it('declares grand-final and post-season titles', () => {
    expect(INDEX).toContain('AFL Grand Final Tips & Prediction')
    expect(INDEX).toContain('AFL Premiers')
  })

  it('keeps the regular-state title / og:title strings untouched', () => {
    expect(INDEX).toContain("'AFL Tips & Predictions'")
    expect(INDEX).toContain("'AFL Tips & Predictions | AI-Powered Footy Tipping'")
  })

  it('keeps the canonical link derived from siteUrl', () => {
    expect(INDEX).toMatch(/rel:\s*'canonical'/)
    expect(INDEX).toContain('siteUrl')
  })
})

// ---------------------------------------------------------------------------
// GrandFinalReport component contract — source-grep
// ---------------------------------------------------------------------------
describe('GrandFinalReport component contract', () => {
  it('renders all report sections', () => {
    expect(GF_REPORT).toContain('Grand Final') // hero eyebrow
    expect(GF_REPORT).toContain('The Verdict')
    expect(GF_REPORT).toContain('Season Story')
    expect(GF_REPORT).toContain('Keys to the Game')
    expect(GF_REPORT).toContain('Players to Watch')
    expect(GF_REPORT).toContain('Injury Watch')
    expect(GF_REPORT).toContain('Conditions &amp; X-Factor')
    expect(GF_REPORT).toContain('Talking Points')
    // GF-DESIGN: heuristic tips surface directly in the report
    expect(GF_REPORT).toContain('The Tips')
  })

  it('is optional-safe: every list-backed section is guarded', () => {
    expect(GF_REPORT).toMatch(/v-if="seasonStorySides\.length > 0"/)
    expect(GF_REPORT).toMatch(/v-if="keys\.length > 0"/)
    expect(GF_REPORT).toMatch(/v-if="homePlayers\.length > 0 \|\| awayPlayers\.length > 0"/)
    expect(GF_REPORT).toMatch(/v-if="homeInjuries\.length > 0 \|\| awayInjuries\.length > 0"/)
    expect(GF_REPORT).toMatch(/v-if="talkingPoints\.length > 0"/)
    expect(GF_REPORT).toMatch(/v-if="weatherVisible \|\| xFactorVisible"/)
  })

  it('GF-CONTENT: hides "unavailable" filler and placeholder players', () => {
    // The agent once emitted a "Weather unavailable" paragraph and an
    // "Unknown" player entry — both are filtered out defensively.
    expect(GF_REPORT).toMatch(/isUnavailableNote/)
    expect(GF_REPORT).toMatch(/isPlaceholderPlayer/)
    expect(GF_REPORT).toMatch(/toLowerCase\(\) === 'unknown'/)
  })

  it('GF-DESIGN: editorial layout — no section cards, hairline sections', () => {
    // Section = hairline rule + numbered label; no per-section card box.
    expect(GF_REPORT).toMatch(/class="section-label"/)
    expect(GF_REPORT).not.toMatch(/class="gf-section card/)
    expect(GF_REPORT).toMatch(/border-top: 1px solid var\(--color-border\)/)
  })

  it('normalises talking-point copy with formatExplanationImpl', () => {
    expect(GF_REPORT).toMatch(/formatExplanationImpl/)
  })

  it('binds report fields that exist on the GrandFinalReport interface', () => {
    // Every `report.<field>` path the template binds must be declared on
    // the interface — a cheap shape check in lieu of a mounted fixture.
    const bindings = new Set<string>()
    const re = /report\.[A-Za-z_][A-Za-z0-9_]*/g
    let m: RegExpExecArray | null
    while ((m = re.exec(GF_REPORT)) !== null) bindings.add(m[0])
    expect(bindings.size).toBeGreaterThan(0)

    const interfaceBody = USE_API.match(
      /export interface GrandFinalReport \{[\s\S]*?\n\}/,
    )
    expect(interfaceBody).not.toBeNull()
    for (const binding of bindings) {
      const field = binding.split('.')[1]
      expect(
        interfaceBody![0],
        `template binds report.${field} which is not on GrandFinalReport`,
      ).toMatch(new RegExp(`\\b${field}:`))
    }
  })

  it('uses monochrome design tokens only (no hex colours)', () => {
    expect(GF_REPORT).toMatch(/var\(--color-/)
    expect(GF_REPORT).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })

  it('renders team logos with the required image attributes', () => {
    const imgs = GF_REPORT.match(/<img\b[^>]*\/?>/g) ?? []
    expect(imgs.length).toBeGreaterThan(0)
    for (const tag of imgs) {
      expect(tag).toMatch(/loading="lazy"/)
      expect(tag).toMatch(/decoding="async"/)
      expect(tag).toMatch(/alt="[^"]+"/)
      expect(tag).toMatch(/width="/)
      expect(tag).toMatch(/height="/)
    }
  })
})

// ---------------------------------------------------------------------------
// OffSeasonCelebration component contract — source-grep
// ---------------------------------------------------------------------------
describe('OffSeasonCelebration component contract', () => {
  it('accepts nullable premier/season props', () => {
    expect(CELEBRATION).toMatch(/premier:\s*string\s*\|\s*null/)
    expect(CELEBRATION).toMatch(/season:\s*number\s*\|\s*null/)
  })

  it('handles a missing premier with a generic message', () => {
    expect(CELEBRATION).toMatch(/<template v-else>/)
    expect(CELEBRATION).toContain('Off Season')
  })

  it('fires the premier-colour confetti client-side only', () => {
    expect(CELEBRATION).toMatch(/import confetti from 'canvas-confetti'/)
    expect(CELEBRATION).toMatch(/getTeamColors\(props\.premier\)/)
    expect(CELEBRATION).toMatch(/onMounted\(celebrate\)/)
  })

  it('keeps monochrome typography (no hex colours)', () => {
    expect(CELEBRATION).toMatch(/var\(--color-/)
    expect(CELEBRATION).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })

  it('renders the premier logo with the required image attributes', () => {
    const imgs = CELEBRATION.match(/<img\b[^>]*\/?>/g) ?? []
    expect(imgs.length).toBeGreaterThan(0)
    for (const tag of imgs) {
      expect(tag).toMatch(/loading="lazy"/)
      expect(tag).toMatch(/decoding="async"/)
      expect(tag).toMatch(/alt="[^"]+"/)
      expect(tag).toMatch(/width="/)
      expect(tag).toMatch(/height="/)
    }
  })
})
