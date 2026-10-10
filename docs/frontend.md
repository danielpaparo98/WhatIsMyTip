# WhatIsMyTip Frontend Documentation

## Overview

The WhatIsMyTip frontend is a Nuxt 4 application with a monochrome bold typographic design. It provides a clean, modern interface for viewing AFL and rugby-league tips (NRL, NRLW, State of Origin), state-league pages, game/match information, and backtest results.

## Project Structure

```
frontend/
├── app.vue                    # Root component
├── error.vue                  # Error boundary
├── nuxt.config.ts             # Nuxt configuration
├── package.json               # Node dependencies
├── assets/
│   └── css/
│       └── main.css           # Global styles and design system
├── components/                # Vue components
│   ├── ConfettiEffect.vue       # End-of-season celebration effect
│   ├── Footer.vue               # Site footer
│   ├── GrandFinalReport.vue     # Grand-final pre-match report
│   ├── Header.vue               # Site header with navigation
│   ├── LeagueSelector.vue       # League dropdown (persists the selection)
│   ├── MatchAnalysisCard.vue    # Pre-game analysis breakdown
│   ├── ModelCoefficientChart.vue  # Learned model coefficients chart
│   ├── OffSeasonBanner.vue      # Off-season banner
│   ├── OffSeasonCelebration.vue # Premier celebration for settled seasons
│   ├── PerformanceView.vue      # League performance view
│   ├── RugbyLeagueTipsView.vue  # Shared rugby-league tips view (nrl/nrlw/origin)
│   ├── TipCard.vue              # Tip display component
│   └── WeatherCard.vue          # Match-day weather widget
├── composables/               # Vue composables
│   ├── useApi.ts              # API communication composable
│   ├── useBoostedShap.ts      # Boosted-tip SHAP importance data
│   ├── useChartTheme.ts       # Shared chart.js theme/options
│   ├── useColorMode.ts        # Colour-mode preference
│   ├── useFormatters.ts       # Currency / percentage / date helpers
│   ├── useGameSlug.ts         # Game slug helpers
│   ├── useLatestRound.ts      # Latest-round derivation for league pages
│   ├── useLeagueColors.ts     # Per-club brand palettes (state leagues + rugby-league)
│   ├── useLeagueEvents.ts     # League event fetching + current-round helpers
│   ├── useLeagueMatchRoute.ts # Pure /{league}/match/{slug} route validation rules
│   ├── useLeagueRedirect.ts   # Pure remembered-league redirect decision
│   ├── usePastSeason.ts       # Past-season selection for performance pages
│   ├── useRugbyLeagueTips.ts  # Pure rugby-league tips-page helpers (model strip, round strip)
│   ├── useSeasonState.ts      # Pure season-state + premier derivation for league pages
│   ├── useSportConfig.ts      # SportConfig per league (LEAGUES, labels, nouns, timezone)
│   ├── useTeamColors.ts       # AFL club colour arrays
│   ├── useTeamIdentity.ts     # Crest-or-initials badge resolution per club
│   └── useTeamLogos.ts        # Resolve team logo paths
├── layouts/
│   └── default.vue            # Default page layout
├── lib/
│   └── leagueRoutes.ts        # Pure league-route enumeration (prerender hook + sitemap)
├── middleware/
│   └── league-redirect.global.ts  # Remembered-league redirect on initial page load
├── pages/                     # Page routes
│   ├── index.vue              # Home page with tips (AFL)
│   ├── about.vue              # About page
│   ├── backtest.vue           # Backtesting results page
│   ├── performance.vue        # AFL performance page
│   ├── game/
│   │   └── [slug].vue         # Per-game detail page (AFL)
│   ├── nrl/
│   │   └── index.vue          # Rugby-league tips page (/nrl)
│   ├── nrlw/
│   │   └── index.vue          # Rugby-league tips page (/nrlw)
│   ├── origin/
│   │   └── index.vue          # Rugby-league tips page (/origin)
│   └── [league]/
│       ├── index.vue          # League home page (/{league})
│       ├── performance.vue    # League performance page (/{league}/performance)
│       └── match/
│           └── [slug].vue     # League match detail page (/{league}/match/{slug})
├── public/                    # Static assets (team logos, robots.txt, sitemap.xml)
├── server/
│   └── routes/
│       └── sitemap.xml.ts     # Prerendered sitemap (all league routes)
└── tests/
    ├── unit/                  # Vitest unit tests (incl. league-routes, league-redirect,
    │                          #   season-state, team-identity-badge, league-match-page)
    └── game-detail-flow.spec.ts  # Playwright end-to-end test
```

## Dependencies

This project uses **bun** for JavaScript/TypeScript dependency management. The dependencies are defined in [`package.json`](frontend/package.json:1).

### Core Dependencies

- **nuxt** (^4.0.0) - Full-stack Vue.js framework with SSR
- **@nuxtjs/tailwindcss** (^6.12.0) - Tailwind CSS integration
- **chart.js** (^4.5.1) - Chart rendering engine
- **vue-chartjs** (^5.3.3) - Vue 3 wrapper around chart.js

### Development Dependencies

- **@nuxt/eslint** (^0.7.0) - ESLint integration
- **@playwright/test** (^1.58.2) - End-to-end browser tests
- **typescript** (^5.7.0) - TypeScript support
- **@tailwindcss/forms** (^0.5.9) - Form styling utilities

## Installation

### Prerequisites

- **bun** (JavaScript runtime and package manager)
- **Node.js** 18+ (for Nuxt 4)

### Install Dependencies

```bash
cd frontend
bun install
```

This command will install all dependencies from [`package.json`](frontend/package.json:1).

## Configuration

### Nuxt Configuration ([`nuxt.config.ts`](frontend/nuxt.config.ts:1))

The Nuxt configuration includes:

- **DevTools**: Enabled for development
- **Tailwind CSS**: Integrated module
- **Static Generation**: Preset configured for production builds
- **SEO**: Meta tags and Open Graph tags configured
- **API Configuration**: Public API base URL configured

### Environment Variables

The frontend uses environment variables for configuration:

```bash
# Local development
API_BASE_URL=http://localhost:8000

# Production (FastAPI service on App Platform, same-origin /api route)
API_BASE_URL=https://whatismytip.com/api
```

The backend API is served by a single FastAPI container.  In production App Platform routes `/api/*` to it on the same hostname as the frontend, so `API_BASE_URL` is the public domain + `/api` (e.g., `https://whatismytip.com/api`).  No reverse proxy is in the path.

## Design System

### Monochrome Bold Typographic Design

The frontend uses a bold, monochrome design with high contrast:

#### Color Palette

```css
--color-bg: #ffffff;      /* Background color */
--color-text: #000000;    /* Primary text color */
--color-border: #e5e5e5;  /* Border color */
--color-muted: #666666;   /* Muted text color */
--color-hover: #f5f5f5;   /* Hover background color */
```

#### Typography

**Bold weights** are used for headings:
- `h1`: 800 weight, clamp(2.5rem, 8vw, 5rem)
- `h2`: 800 weight, clamp(2rem, 5vw, 3.5rem)
- `h3`: 800 weight, clamp(1.5rem, 4vw, 2.5rem)

**Body text** uses system fonts with 1.7 line height:
- Font: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif
- Size: 1.125rem
- Color: Muted gray

#### Components

**Buttons**:
- Full border with bold text
- Hover: background becomes text color, text becomes background
- Active: slight scale down (0.98)

**Cards**:
- Minimalist border design
- Subtle hover effect on border color

**Links**:
- Underlined with offset
- Smooth opacity transition on hover

### Utility Classes

The design system includes utility classes in [`main.css`](frontend/assets/css/main.css:129):

- `.text-bold` - Bold text
- `.text-black` - Black text
- `.text-muted` - Muted text
- `.border-top` - Top border
- `.border-bottom` - Bottom border

## Components

### Header ([`components/Header.vue`](frontend/components/Header.vue:1))

Site header with:
- Logo/brand name
- Navigation links
- Responsive design

### Footer ([`components/Footer.vue`](frontend/components/Footer.vue:1))

Site footer with:
- Copyright information
- Links to pages
- Social media links

### RugbyLeagueTipsView ([`components/RugbyLeagueTipsView.vue`](frontend/components/RugbyLeagueTipsView.vue:1))

The shared view behind the three rugby-league pages (`/nrl`, `/nrlw`, `/origin`):
- Round strip (`Round {n} • {season}`, `GF • {season}` once finals are reached)
- The reduced 4-model tip strip — **Elo Rating**, **Form**, **Home Advantage**, **Matchup** — labelled from the league's `SportConfig` (`RUGBY_LEAGUE_CONFIG`)
- Club identity via generated initials badges coloured from the hand-curated `useLeagueColors` palettes — no NRL logo assets exist, so the identity path is color-only by design

Other components: `LeagueSelector` (league dropdown), `OffSeasonBanner`,
`OffSeasonCelebration` + `ConfettiEffect` (end-of-season premier celebration),
`PerformanceView`, `GrandFinalReport`, `ModelCoefficientChart`.

### TipCard ([`components/TipCard.vue`](frontend/components/TipCard.vue:1))

Displays a tipping prediction with:
- Selected team
- Confidence level
- Predicted margin
- AI explanation (if available)
- Heuristic type badge

## Pages

### Home Page ([`pages/index.vue`](frontend/pages/index.vue:1))

Main page showing:
- Recent tips
- Game cards
- Navigation to other pages

### About Page ([`pages/about.vue`](frontend/pages/about.vue:1))

Information page about:
- Project description
- Features overview
- How it works

### Backtest Page ([`pages/backtest.vue`](frontend/pages/backtest.vue:1))

Backtesting results page with:
- Season and round selection
- Heuristic comparison
- Performance metrics
- Historical results

### League Routes & Redirect

> LEAGUE-ROUTES (2026-09-30, user request)

The thirteen non-AFL leagues are first-class URLs of the site, while AFL keeps the root path. **Why:** every league deserves a shareable, SEO-indexable home (and match) URL of its own, while the AFL's search presence stays consolidated on `/`.

#### Routes

- **AFL** lives at `/` (home) and `/game/{slug}` (match detail) — unchanged.
- **Every other league** has its own home page at `/{league}`: the ten state leagues (`/wafl`, `/waflw`, `/vfl`, `/vflw`, `/sanfl`, `/aflw`, `/qafl`, `/qaflw`, `/nwfl`, `/sfl`) and the three rugby-league competitions (`/nrl`, `/nrlw`, `/origin`).
- **Match pages** live at `/{league}/match/{slug}` and render `GET /api/events/{slug}` data. Unknown league keys, malformed slugs, and another league's slug all 404; `/afl/match/{slug}` redirects to `/` since AFL matches live on the legacy surfaces.
- All of these are prerendered and listed in `sitemap.xml` via the shared enumeration in [`lib/leagueRoutes.ts`](frontend/lib/leagueRoutes.ts:1): `/{league}` for all thirteen keys (even when a competition is not synced yet — the page degrades gracefully), plus `/{league}/match/{slug}` for each event of the league's derived current round.

#### Rugby-league pages (`/nrl`, `/nrlw`, `/origin`)

Each rugby-league competition gets a dedicated thin page (`pages/nrl/index.vue`, etc.) rendering only `<RugbyLeagueTipsView league="…"/>` — static segments outrank the `pages/[league]` dynamic param, so the shared state-league page stays untouched. All labels, the contest noun (`Match`) and the display timezone (`Australia/Brisbane`) flow from `RUGBY_LEAGUE_CONFIG` in [`useSportConfig.ts`](frontend/composables/useSportConfig.ts:1); the sport's reduced model set (`elo`, `form`, `home_advantage`, `matchup`) is the whole strip — no AFL-only heuristics or unsourced models leak in.

#### Remember-redirect rule ([`middleware/league-redirect.global.ts`](frontend/middleware/league-redirect.global.ts:1))

On the app's **initial page load of `/` only** (hard load / external entry — never internal client-side navigation), a visitor whose persisted league (`localStorage['wimt-league']`) is a valid non-AFL league is redirected to `/{league}` with `replace: true` (the back button returns to the real entry URL). The redirect is client-only: the prerendered static `/` remains the AFL page for every visitor.

#### Escape hatch

Any **internal** navigation to `/` — the header logo, the league dropdown's AFL choice, the nav "Tips" link — is intentional and **never redirects**. Only the first route resolution may redirect; afterwards the visitor is treated as deliberately choosing to view AFL.

#### End-of-season treatment

- When every event in the season payload is settled, the league page shows a premier celebration (`OffSeasonCelebration` + `ConfettiEffect`) instead of a bare last-round list; the premier is the winner of the latest-dated completed event ([`useSeasonState.ts`](frontend/composables/useSeasonState.ts:1)).
- When the season's highest round is still upcoming, the round strip reads `GF • {season}` instead of `R{n} • {season}`.
- Clubs without logo files render a generated monochrome initials badge coloured from their club's league palette (`useTeamIdentity` + `useLeagueColors`) — no broken images on state-league pages.
- League crests populate from `GET /api/teams` (`syncTeamIdentity`, awaited inside the league/match data handlers so they bake into prerendered HTML): entries with a `logo_url` render the real crest; clubs without one keep the generated initials-badge fallback.

## Composables

### useApi ([`composables/useApi.ts`](frontend/composables/useApi.ts:1))

API communication composable with:
- `getTips()` - Fetch tips from API
- `getGames()` - Fetch games from API
- `runBacktest()` - Run backtest
- `compareHeuristics()` - Compare heuristics
- League-scoped variants (`league` param) for the league backtest endpoints, plus the multi-sport read side (`/api/sports`, `/api/events`)

### useSportConfig ([`composables/useSportConfig.ts`](frontend/composables/useSportConfig.ts:1))

Single source of truth for sport-specific presentation (ADR 0001):
- `LEAGUES` — 14 static entries: AFL first, then the ten state leagues, then the rugby-league competitions `nrl`, `nrlw`, `origin` (appended, never reordered)
- `RUGBY_LEAGUE_CONFIG` — the rugby-league `SportConfig`: contest noun `Match`, stage noun `Round`, display timezone `Australia/Brisbane`, reduced model set (`elo`, `form`, `home_advantage`, `matchup`)
- `getLeagueConfig(key)` — resolves a league key to its config (rugby-league keys share `sportId: 'rugby-league'`; AFL keys fall back to `AFL_CONFIG`)
- The persisted active-league store (`localStorage['wimt-league']`) powering the nav's league-aware home/performance paths

### useRugbyLeagueTips ([`composables/useRugbyLeagueTips.ts`](frontend/composables/useRugbyLeagueTips.ts:1))

Pure presentation helpers for the rugby-league tips pages: the model strip (display order + labels, sourced only from the league's `SportConfig` — a registry/label drift degrades visibly) and the round-strip wording (`Round {n} • {season}` / `GF • {season}`). Dependency-free so the vitest suite can pin the wording contracts without mounting the SFC.

### useLeagueColors ([`composables/useLeagueColors.ts`](frontend/composables/useLeagueColors.ts:1))

Hand-curated per-club brand palettes (`{primary, secondary}`) for the state leagues and the rugby-league competitions, keyed by the raw feed club names (`Broncos`, `Wests Tigers`, `Blues`, `Maroons`, …) and matched case/whitespace-insensitively. Since no NRL logo assets exist, `useTeamIdentity` resolves these palettes into generated initials badges — every club renders a branded crest without a PNG on disk.

## API Integration

The frontend communicates with the backend API using the `useApi` composable.

### Base URL

Default: `http://localhost:8000` (development) or `https://whatismytip.com/api` (production)

Configured via the `API_BASE_URL` environment variable. In production this is the public domain + `/api` (same-hostname routing on App Platform).

### API Calls

#### Fetch Tips

```typescript
const { data, error, loading } = await useApi.getTips({
  heuristic: 'best_bet',
  season: 2025,
  round: 1
})
```

#### Run Backtest

```typescript
const { data, error, loading } = await useApi.runBacktest({
  season: 2024,
  round: 5,
  heuristic: 'best_bet'
})
```

## Running the Frontend

### Development Mode

```bash
cd frontend
bun run dev
```

The application will be available at `http://localhost:3000`.

### Production Build

```bash
cd frontend
bun run build
```

This generates static files in the `.output/public` directory.

### Preview Production Build

```bash
cd frontend
bun run preview
```

## Styling

### Tailwind CSS

The project uses Tailwind CSS for utility-first styling:
- Responsive design
- Dark mode support (future)
- Custom color palette

### Custom CSS

Global styles are defined in [`main.css`](frontend/assets/css/main.css:1):

- Design system variables
- Typography settings
- Component styles
- Animations

## Performance

### Static Generation

The frontend uses Nuxt's static generation preset for optimal performance:
- Pre-rendered HTML
- Fast page loads
- SEO friendly

### Code Splitting

Nuxt automatically splits code by route:
- Only load code for current page
- Smaller initial bundle size

### Image Optimization

Images are optimized automatically by Nuxt.

## Accessibility

The frontend follows accessibility best practices:
- Semantic HTML structure
- Keyboard navigation support
- High contrast colors
- ARIA labels where needed

## Browser Support

- Chrome (latest)
- Firefox (latest)
- Safari (latest)
- Edge (latest)

## Testing

### Development

Use browser developer tools to test:
- Network requests
- Component rendering
- User interactions

### Production Build

Test the production build locally:
```bash
bun run build
bun run preview
```

## Deployment

### Build for Production

```bash
cd frontend
bun run build
```

### Deploy Static Files

Deploy the `.output/public` directory to:
- Netlify
- Vercel
- GitHub Pages
- Any static site host

## Next Steps

- [x] Add unit tests (Vitest — `tests/unit/`)
- [x] Add E2E tests with Playwright (`tests/game-detail-flow.spec.ts`)
- [ ] Implement dark mode
- [ ] Add loading states and skeletons
- [ ] Implement error boundaries
- [ ] Add form validation
- [ ] Implement authentication
- [ ] Add push notifications
- [ ] Implement favorites/bookmarks
- [ ] Add more visualization options
