/**
 * LeagueSelector (top-nav league picker).
 *
 * Covers: rendering the full static league list, the aria contract,
 * and the active-league flow — default AFL, selection updating the
 * shared store, and persistence via localStorage (`wimt-league`,
 * mock-backed by happy-dom's storage).
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import type { Component } from 'vue'
import LeagueSelector from '../../components/LeagueSelector.vue'
import {
  DEFAULT_LEAGUE_KEY,
  LEAGUES,
  LEAGUE_STORAGE_KEY,
  useActiveLeague,
} from '../../composables/useSportConfig'

const SELECT = 'select.league-select'

// The stored selection resolves in onMounted (SSR-safe, mirroring
// useColorMode), so the resulting DOM patch lands on the next tick.
async function mountSelector() {
  const wrapper = mount(LeagueSelector as Component)
  await nextTick()
  return wrapper
}

beforeEach(() => {
  window.localStorage.clear()
})

describe('LEAGUES registry', () => {
  it('starts with AFL followed by the state leagues', () => {
    expect(LEAGUES.map((l) => l.key)).toEqual([
      'afl',
      'wafl',
      'waflw',
      'vfl',
      'vflw',
      'sanfl',
      'aflw',
      'qafl',
      'qaflw',
      'nwfl',
      'sfl',
    ])
  })

  it('every league has a key, display name, and short label', () => {
    for (const league of LEAGUES) {
      expect(league.key, `missing key: ${JSON.stringify(league)}`).toBeTruthy()
      expect(league.displayName, `missing displayName: ${league.key}`).toBeTruthy()
      expect(league.shortLabel, `missing shortLabel: ${league.key}`).toBeTruthy()
    }
    expect(new Set(LEAGUES.map((l) => l.key)).size).toBe(LEAGUES.length)
  })
})

describe('LeagueSelector rendering', () => {
  it('renders one option per league, in LEAGUES order', async () => {
    const wrapper = await mountSelector()
    const options = wrapper.findAll('option')
    expect(options).toHaveLength(LEAGUES.length)
    options.forEach((option, i) => {
      expect(option.element.value).toBe(LEAGUES[i].key)
      expect(option.text()).toBe(LEAGUES[i].shortLabel)
    })
  })

  it('has an accessible name on the select control', async () => {
    const wrapper = await mountSelector()
    const select = wrapper.get(SELECT)
    expect(select.attributes('aria-label')).toBe('Select league')
  })
})

describe('active league selection', () => {
  it('defaults to AFL when nothing is stored', async () => {
    const wrapper = await mountSelector()
    expect(wrapper.get(SELECT).element.value).toBe(DEFAULT_LEAGUE_KEY)
    const { activeLeague, activeConfig } = useActiveLeague()
    expect(activeLeague.value).toBe('afl')
    expect(activeConfig.value.displayName).toBe('AFL')
  })

  it('selecting a league updates the active league and persists it', async () => {
    const wrapper = await mountSelector()
    await wrapper.get(SELECT).setValue('sanfl')

    const { activeLeague, activeConfig } = useActiveLeague()
    expect(activeLeague.value).toBe('sanfl')
    expect(activeConfig.value.displayName).toBe('SANFL')
    expect(window.localStorage.getItem(LEAGUE_STORAGE_KEY)).toBe('sanfl')
  })

  it('restores a persisted selection on mount', async () => {
    window.localStorage.setItem(LEAGUE_STORAGE_KEY, 'waflw')
    const wrapper = await mountSelector()

    expect(wrapper.get(SELECT).element.value).toBe('waflw')
    const { activeConfig } = useActiveLeague()
    expect(activeConfig.value.displayName).toBe('WAFLW')
  })

  it('falls back to AFL for unknown stored keys', async () => {
    window.localStorage.setItem(LEAGUE_STORAGE_KEY, 'not-a-league')
    const wrapper = await mountSelector()

    expect(wrapper.get(SELECT).element.value).toBe('afl')
  })

  it('setActiveLeague persists at the storage level and survives re-hydration', () => {
    const first = useActiveLeague()
    first.setActiveLeague('vfl')
    expect(window.localStorage.getItem(LEAGUE_STORAGE_KEY)).toBe('vfl')

    const second = useActiveLeague()
    expect(second.activeLeague.value).toBe('vfl')
    expect(second.activeConfig.value.sportId).toBe('vfl')
  })

  it('ignores setActiveLeague calls with unknown keys', () => {
    const { activeLeague, setActiveLeague } = useActiveLeague()
    setActiveLeague('bogus')
    expect(activeLeague.value).toBe('afl')
    expect(window.localStorage.getItem(LEAGUE_STORAGE_KEY)).toBeNull()
  })
})
