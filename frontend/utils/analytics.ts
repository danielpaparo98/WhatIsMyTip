// Typed SSR-safe wrapper around the Umami analytics script.
//
// Umami injects a global `window.umami` (set by the script tag registered
// conditionally in `plugins/umami.client.ts`).  This wrapper is
// intentionally tiny: it forwards events to the global when present and
// silently no-ops on the server, in dev/branch builds without analytics
// configured, when the script is blocked (ad-blocker, CSP regression,
// network failure), or when `window.umami.track` throws.  Analytics must
// never break the app — every call is best-effort and fire-and-forget.
//
// Page views are tracked automatically by the Umami script on every route
// (client-side navigations included).  Custom events add typed context the
// bare URL cannot carry (which league, which heuristic) and funnel anchors
// that survive URL-scheme changes (/game/{slug} vs /{league}/match/{slug}).

/**
 * Canonical analytics event names — the site's entire custom-event
 * vocabulary. Callers MUST pass one of these to `track()`; the union type
 * makes ad-hoc strings a compile-time error so names cannot drift from the
 * dashboard's saved reports. Add new events here FIRST, then call
 * `track(ANALYTICS_EVENTS.x)`. Retire events with their surfaces (delete,
 * never leave dangling).
 *
 * Conventions:
 *   - snake_case (matches Umami dashboard display and the ResumeRoo fleet)
 *   - past-tense verbs (we log completed actions, not intents)
 *   - grouped by domain prefix (match_*, heuristic_*, …)
 *   - payloads are routing metadata only (league keys, heuristic keys,
 *     status codes) — never user content
 */
export const ANALYTICS_EVENTS = {
  // View anchors — page views are auto-tracked by URL, but these typed
  // events carry the league/heuristic context and let dashboards build
  // funnels that survive route-scheme changes. Fired once per client-side
  // page mount (the wrapper no-ops during prerender).
  homeViewed: 'home_viewed',
  leagueViewed: 'league_viewed',
  matchViewed: 'match_viewed',
  performanceViewed: 'performance_viewed',
  aboutViewed: 'about_viewed',

  // Interactions — the user-selectable tipping heuristic on the home
  // pages (AFL + every league home): which models users actually compare.
  heuristicSelected: 'heuristic_selected',

  // The header league dropdown — which non-default leagues get explored.
  leagueSelectorUsed: 'league_selector_used',

  // Footer supporter CTA — the site's one conversion signal.
  buyMeACoffeeClicked: 'buy_me_a_coffee_clicked',

  // Client-visible error surface (error.vue): 404s measure broken links,
  // 5xx measure client-visible failures. Fires on mount with the status
  // code; never fires for the page's own prerender pass (no window).
  errorPageShown: 'error_page_shown',
} as const

/** Union of all valid event names (derived from the constant). */
export type AnalyticsEventName =
  (typeof ANALYTICS_EVENTS)[keyof typeof ANALYTICS_EVENTS]

// ---------------------------------------------------------------------------
// Ready queue (ANALYTICS-QUEUE, 2026-11-13)
//
// The injection plugin is CLIENT-ONLY (umami.client.ts — the H-1 fix):
// the deferred Umami script enters the DOM during hydration and needs a
// network round-trip before `window.umami` exists. View-anchor events,
// however, fire in `onMounted` — reliably BEFORE the global appears.
// Without buffering, every mount-time event hits the no-op guard and is
// silently lost (verified live in prod: pageviews landed, 100% of custom
// events dropped).
//
// So `track()` buffers events until the global appears. A single lazy
// poll timer flushes the queue in order; the pipeline gives up (and
// disables itself) after READY_TIMEOUT_MS so an ad-blocked script can't
// leave a timer — or a growing queue — behind forever. Once the global
// is present, dispatch is immediate with no queue hop.
// ---------------------------------------------------------------------------
const READY_POLL_MS = 100
const READY_TIMEOUT_MS = 5000
const MAX_QUEUED = 20

interface QueuedEvent {
  name: AnalyticsEventName
  data?: AnalyticsEventData
}

let queue: QueuedEvent[] = []
let flushTimer: ReturnType<typeof setInterval> | null = null

/** True when `window.umami.track` is callable right now. */
function umamiReady(): boolean {
  if (typeof window === 'undefined') return false
  const umami = (window as BrowserWindow).umami
  return !!umami && typeof umami.track === 'function'
}

/** Direct dispatch — only called when the global is present. */
function dispatch(name: AnalyticsEventName, data?: AnalyticsEventData): boolean {
  const umami = (window as BrowserWindow).umami
  try {
    umami?.track(name, data)
    return true
  } catch {
    // The Umami client must never break the host app — swallow and move on.
    // Common causes: ad-blocker probes tripping, CSP regressions, or a stale
    // umami global left by a previous load on a different host.
    return false
  }
}

function stopFlushTimer(): void {
  if (flushTimer !== null) {
    clearInterval(flushTimer)
    flushTimer = null
  }
}

/** Flush the buffered events, in order, then tear the pipeline down. */
function flushQueue(): void {
  const buffered = queue
  queue = []
  stopFlushTimer()
  for (const { name, data } of buffered) dispatch(name, data)
}

function startFlushTimer(): void {
  if (flushTimer !== null) return
  const startedAt = Date.now()
  flushTimer = setInterval(() => {
    if (umamiReady()) {
      flushQueue()
    } else if (Date.now() - startedAt >= READY_TIMEOUT_MS) {
      // Umami never showed up within the window (blocked, stalled,
      // disabled env). Abandon the buffer and tear the pipeline down —
      // but do NOT latch a permanent off-state: if the script arrives
      // later, new events simply take the normal dispatch path.
      queue = []
      stopFlushTimer()
    }
  }, READY_POLL_MS)
}

/**
 * Test seam — resets the module-level queue/timer state. Exported solely
 * for the unit suite; production code must never call this.
 */
export function __resetAnalyticsForTests(): void {
  queue = []
  stopFlushTimer()
}

/**
 * Arbitrary event payload. Umami accepts any JSON-serialisable record; we
 * restrict to JSON primitives so payloads survive SSR serialisation and
 * never trip the structured-clone algorithm on the beacon path.
 */
export type AnalyticsEventData = Record<
  string,
  string | number | boolean | null | undefined
>

/**
 * Shape of the global `window.umami` injected by the Umami script. Declared
 * locally so callers and tests can depend on it without polluting the
 * global `Window` type (which would force every other module to acknowledge
 * the optional global).
 */
export interface UmamiAnalytics {
  track: (name: string, data?: AnalyticsEventData) => void | Promise<void>
}

/** Window with the optional Umami global — used for the in-function cast. */
type BrowserWindow = Window & { umami?: UmamiAnalytics }

/**
 * Send a custom event to Umami. SSR-safe: no-ops on the server, when the
 * script has not loaded, when the global has been blocked, or when `track`
 * throws. Returns `true` if the event was dispatched, `false` otherwise —
 * callers may read the return value (e.g. tests) but MUST NOT gate UI on it
 * (analytics is fire-and-forget).
 *
 * Always pass an `ANALYTICS_EVENTS.*` value: the typed signature prevents
 * vocabulary drift. Ad-hoc strings are a compile-time error.
 */
export function track(
  eventName: AnalyticsEventName,
  eventData?: AnalyticsEventData,
): boolean {
  // The window guard is checked INSIDE the function (never at module
  // top-level) so importing this util during SSR/prerender cannot throw —
  // the module body has no side effects that reach for `window`. SSR
  // never queues: the prerender process has no client to flush into.
  if (typeof window === 'undefined') return false

  // Fast path — the global is present, dispatch right now.
  if (umamiReady()) return dispatch(eventName, eventData)

  // Buffer for the flush loop. Bounded — the funnel-start anchors (the
  // FIRST events a page fires) are the ones worth keeping, so once full
  // the queue stops accepting newer events.
  if (queue.length < MAX_QUEUED) queue.push({ name: eventName, data: eventData })
  startFlushTimer()
  return true
}
