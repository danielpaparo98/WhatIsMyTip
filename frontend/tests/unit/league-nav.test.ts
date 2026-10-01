// LEAGUE-NAV (2026-09-30, user request): nav links stay inside the
// user's league. When browsing a non-AFL league, the header logo and
// the "Tips" link (and the error page's "Back to Home") must land on
// THAT league's home (/{league}) — not bounce to the AFL root. The
// league dropdown remains the explicit way back to AFL.
//
// SFCs can't be mounted under vitest (no Vue plugin), so the wiring is
// pinned with the repo's source-grep convention (same as
// league-selector.test.ts / league-home-page.test.ts), while the
// homePath derivation itself is covered behaviourally in
// useSportConfig.test.ts.
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const ROOT = resolve(__dirname, '../..')

const read = (p: string): string => readFileSync(resolve(ROOT, p), 'utf-8')

describe('league-aware nav links', () => {
  const HEADER = read('components/Header.vue')
  const ERROR = read('error.vue')
  const GAME = read('pages/game/[slug].vue')

  it('header logo links to the shared homePath binding', () => {
    expect(HEADER).toMatch(/const \{ homePath \} = useActiveLeague\(\)/)
    // The logo must bind the league-aware path, not the hardcoded root.
    expect(HEADER).toMatch(/<NuxtLink :to="homePath" class="logo"/)
    expect(HEADER).not.toMatch(/<NuxtLink to="\/" class="logo"/)
  })

  it('header Tips link follows the active league home', () => {
    expect(HEADER).toMatch(/<li><NuxtLink :to="homePath">Tips<\/NuxtLink><\/li>/)
    expect(HEADER).not.toMatch(/<li><NuxtLink to="\/">Tips<\/NuxtLink><\/li>/)
  })

  it('header keeps the aria-label on the logo link', () => {
    // aria-labels.test.ts pins a11y labels; keep the logo's intact
    // through the binding change.
    expect(HEADER).toMatch(/aria-label="WhatIsMyTip home"/)
  })

  it('header Backtest and About stay league-agnostic', () => {
    // Global pages — never hijacked into a league context.
    expect(HEADER).toMatch(/<li><NuxtLink to="\/backtest">Backtest<\/NuxtLink><\/li>/)
    expect(HEADER).toMatch(/<li><NuxtLink to="\/about">About<\/NuxtLink><\/li>/)
  })

  it('error page Back-to-Home follows the active league home', () => {
    expect(ERROR).toMatch(/const \{ homePath \} = useActiveLeague\(\)/)
    expect(ERROR).toMatch(/<NuxtLink :to="homePath" class="back-home"/)
  })

  it('AFL game page back-link stays on the AFL root', () => {
    // /game/[slug] is AFL-only context — "Back to Tips" correctly
    // returns to the AFL home, NOT the stored league.
    expect(GAME).toMatch(/<NuxtLink to="\/" class="back-link">← Back to Tips<\/NuxtLink>/)
  })
})
