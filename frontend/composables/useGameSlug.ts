// Game-slug validation shared by the dynamic /game/[slug] route and
// the corresponding unit test.  The previous regex `^[a-zA-Z0-9]{10,12}$`
// was too strict — the backend's `generate_slug` defaults to length 10
// (lowercase a-z + 0-9) but the Pydantic schema declares `slug: str`
// with no length bound, so a future migration could legitimately produce
// shorter, longer, or hyphenated slugs.  See CR-004 from Phase 2b.

/**
 * Slug validation pattern.
 *
 * Accepts 8–64 chars of [A-Za-z0-9_-] (matches the URL-safe alphabet used
 * by the backend's `generate_slug` plus `-` for future migrations to
 * human-readable slugs like "hawthorn-v-carlton-rd7").
 */
export const SLUG_REGEX = /^[a-zA-Z0-9_-]{8,64}$/

/**
 * Type guard: returns `true` if the input is a syntactically-valid game
 * slug.  Used by the [slug].vue page to short-circuit bad URLs before
 * hitting the backend.
 */
export function isValidGameSlug(slug: unknown): slug is string {
  return typeof slug === 'string' && SLUG_REGEX.test(slug)
}
