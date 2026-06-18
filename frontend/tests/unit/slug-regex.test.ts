import { describe, it, expect } from 'vitest'
import { SLUG_REGEX, isValidGameSlug } from '../../composables/useGameSlug'

// Game-slug validation for the dynamic /game/[slug] route.
//
// The previous regex was `^[a-zA-Z0-9]{10,12}$`, which rejected valid
// slugs produced by the backend (default length is 10, but the field
// is a free-form `str` in the Pydantic schema and may include `-` or
// be longer/shorter than 10–12 in the future).  CR-004 from Phase 2b.

describe('SLUG_REGEX', () => {
  it('accepts a default 10-char alphanumeric slug (matches backend generate_slug default)', () => {
    // Simulates output of backend `generate_slug(10)` — lowercase a-z + 0-9
    expect(SLUG_REGEX.test('a1b2c3d4e5')).toBe(true)
  })

  it('accepts a slug that contains a hyphen (future-proofing)', () => {
    expect(SLUG_REGEX.test('abc-def-123')).toBe(true)
    expect(SLUG_REGEX.test('hello-world')).toBe(true)
  })

  it('accepts the lower bound (8 chars)', () => {
    expect(SLUG_REGEX.test('abcdefgh')).toBe(true)
  })

  it('accepts the upper bound (64 chars)', () => {
    expect(SLUG_REGEX.test('a'.repeat(64))).toBe(true)
  })

  it('rejects a slug that is too short (< 8 chars)', () => {
    expect(SLUG_REGEX.test('short7')).toBe(false)
    expect(SLUG_REGEX.test('a')).toBe(false)
    expect(SLUG_REGEX.test('')).toBe(false)
  })

  it('rejects a slug that is too long (> 64 chars)', () => {
    expect(SLUG_REGEX.test('a'.repeat(65))).toBe(false)
    expect(SLUG_REGEX.test('a'.repeat(100))).toBe(false)
  })

  it('rejects slugs containing characters outside [A-Za-z0-9_-]', () => {
    // Dot, slash, space, unicode, etc. should all fail
    expect(SLUG_REGEX.test('abc.def')).toBe(false)
    expect(SLUG_REGEX.test('abc/def')).toBe(false)
    expect(SLUG_REGEX.test('abc def')).toBe(false)
    expect(SLUG_REGEX.test('abc!def')).toBe(false)
    expect(SLUG_REGEX.test('abc@def')).toBe(false)
  })
})

describe('isValidGameSlug', () => {
  it('returns true for a syntactically-valid slug', () => {
    expect(isValidGameSlug('a1b2c3d4e5')).toBe(true)
    expect(isValidGameSlug('abc-def-123')).toBe(true)
  })

  it('returns false for invalid inputs', () => {
    expect(isValidGameSlug('short')).toBe(false)
    expect(isValidGameSlug('a'.repeat(65))).toBe(false)
    expect(isValidGameSlug('abc.def')).toBe(false)
  })

  it('returns false for non-string inputs', () => {
    expect(isValidGameSlug(undefined)).toBe(false)
    expect(isValidGameSlug(null)).toBe(false)
    expect(isValidGameSlug(123)).toBe(false)
    expect(isValidGameSlug({})).toBe(false)
  })
})
