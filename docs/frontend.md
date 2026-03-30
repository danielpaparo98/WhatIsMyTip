# WhatIsMyTip Frontend Documentation

## Overview

The WhatIsMyTip frontend is a Nuxt 4 application with a monochrome bold typographic design. It provides a clean, modern interface for viewing AFL tips, game information, and backtest results.

## Project Structure

```
frontend/
├── app.vue                    # Root component
├── nuxt.config.ts             # Nuxt configuration
├── package.json               # Node dependencies
├── assets/
│   └── css/
│       └── main.css           # Global styles and design system
├── components/                # Vue components
│   ├── Header.vue             # Site header with navigation
│   ├── Footer.vue             # Site footer
│   ├── GameCard.vue           # Game display component
│   └── TipCard.vue            # Tip display component
├── composables/               # Vue composables
│   └── useApi.ts              # API communication composable
└── pages/                     # Page routes
    ├── index.vue              # Home page with tips
    ├── about.vue              # About page
    └── backtest.vue           # Backtesting results page
```

## Dependencies

This project uses **bun** for JavaScript/TypeScript dependency management. The dependencies are defined in [`package.json`](frontend/package.json:1).

### Core Dependencies

- **nuxt** (^4.0.0) - Full-stack Vue.js framework with SSR
- **@nuxtjs/tailwindcss** (^6.12.0) - Tailwind CSS integration

### Development Dependencies

- **@nuxt/eslint** (^0.7.0) - ESLint integration
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
API_BASE_URL=http://localhost:8000  # Backend API URL
```

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

### GameCard ([`components/GameCard.vue`](frontend/components/GameCard.vue:1))

Displays a single game with:
- Home team and away team
- Venue information
- Date and time
- Visual game card layout

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

## Composables

### useApi ([`composables/useApi.ts`](frontend/composables/useApi.ts:1))

API communication composable with:
- `getTips()` - Fetch tips from API
- `getGames()` - Fetch games from API
- `generateTips()` - Generate new tips
- `runBacktest()` - Run backtest
- `compareHeuristics()` - Compare heuristics

## API Integration

The frontend communicates with the backend API using the `useApi` composable.

### Base URL

Default: `http://localhost:8000`

Can be configured via environment variable `API_BASE_URL`.

### API Calls

#### Fetch Tips

```typescript
const { data, error, loading } = await useApi.getTips({
  heuristic: 'best_bet',
  season: 2025,
  round: 1
})
```

#### Generate Tips

```typescript
const { data, error, loading } = await useApi.generateTips({
  season: 2025,
  round: 1,
  heuristics: ['best_bet', 'yolo'],
  generate_explanations: true
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

- [ ] Add unit tests
- [ ] Add E2E tests with Playwright
- [ ] Implement dark mode
- [ ] Add loading states and skeletons
- [ ] Implement error boundaries
- [ ] Add form validation
- [ ] Implement authentication
- [ ] Add push notifications
- [ ] Implement favorites/bookmarks
- [ ] Add more visualization options
