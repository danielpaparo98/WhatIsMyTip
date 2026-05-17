# WhatIsMyTip — Design System & Aesthetic Stocktake

> A comprehensive reference of the visual design, layout patterns, component architecture, and styling conventions used in the WhatIsMyTip frontend. Written to be **portable** — use this document to recreate the same aesthetic on a new project.

---

## Table of Contents

1. [Design Philosophy](#1-design-philosophy)
2. [Technology Stack](#2-technology-stack)
3. [Color System](#3-color-system)
4. [Typography](#4-typography)
5. [Spacing & Layout](#5-spacing--layout)
6. [Breakpoint Strategy](#6-breakpoint-strategy)
7. [Component Library](#7-component-library)
8. [Interactive Patterns](#8-interactive-patterns)
9. [Charts & Data Visualisation](#9-charts--data-visualisation)
10. [Page Templates](#10-page-templates)
11. [Accessibility](#11-accessibility)
12. [Animation & Transitions](#12-animation--transitions)
13. [Iconography & Imagery](#13-iconography--imagery)
14. [SEO & Meta Patterns](#14-seo--meta-patterns)
15. [File & Folder Conventions](#15-file--folder-conventions)
16. [Replication Checklist](#16-replication-checklist)

---

## 1. Design Philosophy

**"Monochrome with bold typographic design"** — the CSS source file literally opens with this comment.

The aesthetic is **Swiss-style editorial minimalism**: pure black-and-white with sharp typographic hierarchy, generous whitespace, and zero decorative ornamentation. The only colour accents appear in semantic contexts (profit/loss indicators, chart data series, status badges, and the Buy Me a Coffee button).

### Core Principles

| Principle | Implementation |
|---|---|
| **Monochrome-first** | Black `#000` on white `#fff`; grey for secondary text and borders |
| **Type as hero** | Oversized `clamp()` headings with tight leading; `font-weight: 800` |
| **Border-defined cards** | 1 px solid borders, no shadows (except hover states) |
| **Editorial spacing** | Large section padding (3–6 rem), generous card padding (1.25–2 rem) |
| **Zero decoration** | No gradients (except one current-season banner), no rounded corners on cards, no drop shadows at rest |
| **Content-width constraint** | `max-width: 1400px` for nav/footer; `max-width: 80ch` for reading pages |

---

## 2. Technology Stack

| Layer | Technology | Version |
|---|---|---|
| Framework | Nuxt | 4.x |
| UI Framework | Vue | 3.x (Composition API, `<script setup>`) |
| CSS Framework | Tailwind CSS (via `@nuxtjs/tailwindcss`) | 6.12.x |
| CSS Plugin | `@tailwindcss/forms` | 0.5.x |
| Charts | Chart.js + vue-chartjs | 4.5.x / 5.3.x |
| Language | TypeScript | 5.7.x |
| Runtime | Bun | 1.x |
| Linting | `@nuxt/eslint` | 0.7.x |
| E2E Tests | Playwright | 1.58.x |
| Analytics | Umami (self-hosted) | — |

> **Note:** Tailwind is included but the project primarily uses **scoped CSS with CSS custom properties** rather than Tailwind utility classes in templates. Tailwind provides the reset (`@tailwind base`) and form styling (`@tailwindcss/forms`).

---

## 3. Color System

### 3.1 CSS Custom Properties (Design Tokens)

Defined in [`main.css`](frontend/assets/css/main.css:6):

```css
:root {
  --color-bg: #ffffff;
  --color-text: #000000;
  --color-border: #e5e5e5;
  --color-muted: #666666;
  --color-hover: #f5f5f5;
}
```

### 3.2 Semantic Colours (in-context)

| Purpose | Value | Usage |
|---|---|---|
| **Positive / Profit** | `#00a000` | `.positive` class on profit values |
| **Negative / Loss** | `#c00000` | `.negative` class on loss values |
| **Warning / Amber accent** | `#f59e0b` | Match analysis card left border, data warning strong text, disclaimer border |
| **Warning background** | `rgba(255, 193, 7, 0.1)` | Data warning background |
| **Warning border** | `rgba(255, 193, 7, 0.3)` | Data warning border |
| **Success / Completed** | `#10b981` | Status badges (completed games, "Current Season" badge) |
| **Current season gradient** | `linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%)` | Backtest current season section background |
| **Buy Me a Coffee** | `#FFDD00` bg / `#000` text | Footer CTA button |
| **Buy Me a Coffee hover** | `#F5CC00` | Footer CTA hover state |

### 3.3 Chart Colours

Defined in [`useChartTheme.ts`](frontend/composables/useChartTheme.ts:1):

| Heuristic | Border | Background |
|---|---|---|
| **Best Bet** | `#3b82f6` (blue-500) | `rgba(59, 130, 246, 0.8)` |
| **High Risk / High Reward** | `#f97316` (orange-500) | `rgba(249, 115, 22, 0.8)` |
| **YOLO** | `#ef4444` (red-500) | `rgba(239, 68, 68, 0.8)` |
| **Default / Fallback** | `#6b7280` (gray-500) | `rgba(107, 114, 128, 0.8)` |

### 3.4 Heuristic Accent Borders (Game Detail)

| Heuristic | Left Border Colour |
|---|---|
| Best Bet | `#10b981` (emerald-500) |
| YOLO | `#f97316` (orange-500) |
| High Risk | `#8b5cf6` (violet-500) |

### 3.5 Replication Palette Summary

To recreate this palette on a new project you need:

- **2 neutrals** (black, white)
- **3 grey shades** (border `#e5e5e5`, muted text `#666666`, hover bg `#f5f5f5`)
- **2 semantic colours** (green for positive, red for negative)
- **1 accent colour** (amber `#f59e0b` for warnings/highlights)
- **1 success colour** (emerald `#10b981` for status)
- **3–4 data series colours** (blue, orange, red, violet) for charts
- **1 CTA colour** (yellow `#FFDD00`) if using Buy Me a Coffee pattern

---

## 4. Typography

### 4.1 Font Stack

```css
font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
```

No custom web fonts. Relies on the native system font stack for zero-latency rendering.

### 4.2 Font Rendering

```css
-webkit-font-smoothing: antialiased;
-moz-osx-font-smoothing: grayscale;
```

### 4.3 Type Scale

All headings use `clamp()` for fluid sizing. Base body text is `1.125rem` (18 px).

| Element | Size | Weight | Letter-Spacing | Line-Height |
|---|---|---|---|---|
| **h1** | `clamp(2.5rem, 8vw, 5rem)` | 800 | `-0.02em` | 1.1 |
| **h2** | `clamp(2rem, 5vw, 3.5rem)` | 800 | `-0.02em` | 1.15 |
| **h3** | `clamp(1.5rem, 4vw, 2.5rem)` | 800 | `-0.02em` | 1.2 |
| **h4** | `clamp(1.25rem, 3vw, 2rem)` | 800 | `-0.02em` | 1.25 |
| **p** | `1.125rem` | 400 (normal) | — | 1.7 |
| **Nav links** | `0.875rem` | 700 | `0.05em` | — |
| **Heuristic badges** | `0.6875rem` | 700 | `0.1em` | — |
| **Stat labels** | `0.6875rem`–`0.8125rem` | 600–700 | `0.05em`–`0.1em` | — |
| **Table headers** | `0.6875rem` | 700 | `0.05em` | — |

### 4.4 Hero Headings (Overrides)

The homepage hero uses an even larger h1:

```css
/* Homepage hero */
.hero h1 {
  font-size: clamp(2rem, 8vw, 6rem);
  line-height: 1.05;
}

/* Desktop override */
@media (min-width: 1025px) {
  .hero h1 {
    font-size: clamp(3rem, 10vw, 6rem);
    line-height: 0.95;
  }
}
```

### 4.5 Text Casing Conventions

| Context | Transform | Letter-Spacing |
|---|---|---|
| Nav links | `uppercase` | `0.05em` |
| Buttons | `uppercase` | `0.05em` |
| Heuristic badges | `uppercase` | `0.1em` |
| Stat labels | `uppercase` | `0.1em` |
| Table headers | `uppercase` | `0.05em` |
| Meta labels | `uppercase` | `0.05em` |
| Body text, headings | `none` | — |

---

## 5. Spacing & Layout

### 5.1 Global Container Widths

| Context | Max-Width |
|---|---|
| Nav / Footer | `1400px` |
| About / content pages | `80ch` |
| Game detail page | `1200px` |
| Homepage sections | Full-width (no max-width, padding only) |

### 5.2 Section Padding

| Breakpoint | Section Padding | Hero Padding |
|---|---|---|
| **Mobile** (≤640px) | `2rem 1rem` | `2.5rem–3rem 1rem` |
| **Tablet** (641–1024px) | `3rem 1.5rem` | `3.5rem 1.5rem` |
| **Desktop** (≥1025px) | `4rem 2rem` | `4–6rem 2rem` |

### 5.3 Card Padding

| Breakpoint | Card Padding |
|---|---|
| **Mobile** | `1rem`–`1.25rem` |
| **Tablet** | `1.25rem`–`1.5rem` |
| **Desktop** | `1.5rem`–`2rem` |

### 5.4 Grid Layouts

| Context | Grid | Min Column Width |
|---|---|---|
| **Games grid (homepage)** | `repeat(auto-fit, minmax(280px, 1fr))` | 280px |
| **Games grid (desktop)** | `repeat(auto-fit, minmax(350px, 1fr))` | 350px |
| **Backtest stat cards** | `repeat(auto-fit, minmax(250px, 1fr))` | 250px |
| **Current season cards** | `repeat(auto-fit, minmax(280px, 1fr))` | 280px |
| **Charts grid** | `repeat(auto-fit, minmax(350px, 1fr))` | 350px |
| **Charts grid (desktop)** | `repeat(auto-fit, minmax(500px, 1fr))` | 500px |
| **Tips grid (game detail)** | `repeat(auto-fit, minmax(300px, 1fr))` | 300px |
| **Models grid (game detail)** | `repeat(auto-fit, minmax(250px, 1fr))` | 250px |
| **Stat grid (2-col)** | `1fr 1fr` | — |
| **Game meta** | `repeat(auto-fit, minmax(200px, 1fr))` | 200px |

### 5.5 Gap Sizes

| Context | Gap |
|---|---|
| Grid layouts (mobile) | `1rem` |
| Grid layouts (default) | `1.5rem` |
| Grid layouts (desktop) | `2rem` |
| Nav links | `2rem` |
| Heuristic selector buttons | `0.5rem` |
| Footer content (vertical) | `0.5rem` (paragraphs) |

---

## 6. Breakpoint Strategy

Three breakpoints using **mobile-first** media queries:

| Name | Width | Target |
|---|---|---|
| **Mobile** | ≤ 640px | Phones |
| **Tablet** | 641px – 1024px | Small tablets / laptops |
| **Desktop** | ≥ 1025px | Desktops / large laptops |

### Breakpoint Pattern in Components

Every component follows this structure in `<style scoped>`:

```css
/* Base (mobile) styles */
.component { ... }

/* Mobile styles */
@media (max-width: 640px) {
  .component { ... }
}

/* Tablet styles */
@media (min-width: 641px) and (max-width: 1024px) {
  .component { ... }
}

/* Desktop styles */
@media (min-width: 1025px) {
  .component { ... }
}
```

---

## 7. Component Library

### 7.1 Header

**File:** [`Header.vue`](frontend/components/Header.vue:1)

| Property | Value |
|---|---|
| Layout | Flex row (mobile: column) |
| Position | `sticky`, `top: 0`, `z-index: 50` |
| Border | `border-bottom: 1px solid var(--color-border)` |
| Background | `var(--color-bg)` (opaque white) |
| Logo font | `1.25rem`, weight 800, letter-spacing `-0.02em` |
| Nav link font | `0.875rem`, weight 700, uppercase, letter-spacing `0.05em` |
| Active link | `text-decoration: none` (no underline) |
| Hover | `color: var(--color-muted)` |

### 7.2 Footer

**File:** [`Footer.vue`](frontend/components/Footer.vue:1)

| Property | Value |
|---|---|
| Border | `border-top: 1px solid var(--color-border)` |
| Padding | `2rem 1.5rem` |
| Margin-top | `3rem` |
| Text alignment | Centre |
| Text size | `0.875rem` |
| Buy Me a Coffee | Yellow `#FFDD00` pill, `border-radius: 6px`, hover lifts `-1px` with shadow |

### 7.3 GameCard

**File:** [`GameCard.vue`](frontend/components/GameCard.vue:1)

| Property | Value |
|---|---|
| Border | `1px solid var(--color-border)` |
| Padding | `1.25rem` (mobile: `1rem`, desktop: `1.5rem`) |
| Structure | Header (round + date) → Body (home logo + VS + away logo) → Venue |
| Team logos | `56px` (mobile: `48px`, tablet: `60px`, desktop: `64px`) |
| Score font | `1.75rem`, weight 800 |
| VS text | `0.8125rem`, weight 700 |

### 7.4 TipCard

**File:** [`TipCard.vue`](frontend/components/TipCard.vue:1)

| Property | Value |
|---|---|
| Border | `1px solid var(--color-border)` |
| Hover | `border-color: var(--color-text)` |
| Padding | `1.25rem` (mobile: `1rem`, desktop: `1.5rem`) |
| Structure | Header (heuristic label + confidence %) → Body (team name + margin) → Explanation |
| Heuristic label | `0.6875rem`, uppercase, letter-spacing `0.1em`, muted colour |
| Confidence | `0.8125rem`, weight 700 |
| Team name | `1.25rem` (desktop: `1.5rem`) |
| Explanation | `0.875rem`, line-height 1.5 |

### 7.5 MatchAnalysisCard

**File:** [`MatchAnalysisCard.vue`](frontend/components/MatchAnalysisCard.vue:1)

| Property | Value |
|---|---|
| Border | `1px solid var(--color-border)` + **amber left accent** `4px solid #f59e0b` |
| Hover | `border-color: #f59e0b` (entire border goes amber) |
| Padding | `1.5rem` (mobile: `1rem`) |
| Header | Emoji icon `🗣️` + title "BBQ Talking Points" in amber + italic subtitle |
| Talking points | Left border `2px solid rgba(245, 158, 11, 0.3)`, `padding-left: 1rem` |

### 7.6 Chart Containers (Shared)

Used by [`AccuracyChart.vue`](frontend/components/AccuracyChart.vue:1), [`ProfitChart.vue`](frontend/components/ProfitChart.vue:1), [`CumulativeProfitChart.vue`](frontend/components/CumulativeProfitChart.vue:1):

| Property | Value |
|---|---|
| Border | `1px solid var(--color-border)` |
| Border-radius | `0.5rem` |
| Padding | `1.25rem` (mobile: `1rem`, desktop: `1.5rem`) |
| Chart height | `350px` (mobile: `400px`, desktop: `400px`) |
| Title | `1rem`, weight 600, centre-aligned |
| Loading spinner | `36px` circle, `3px` border |

### 7.7 Buttons

**Global button classes** from [`main.css`](frontend/assets/css/main.css:81):

```css
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: centre;
  padding: 0.875rem 2rem;
  font-size: 1rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border: 2px solid var(--color-text);
  background: var(--color-bg);
  color: var(--color-text);
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn:hover {
  background: var(--color-text);
  color: var(--color-bg);
}

.btn:active {
  transform: scale(0.98);
}

.btn-primary {
  background: var(--color-text);
  color: var(--color-bg);
}

.btn-primary:hover {
  background: var(--color-bg);
  color: var(--color-text);
}
```

### 7.8 Heuristic Selector (Tab Group)

From [`index.vue`](frontend/pages/index.vue:332):

```css
.heuristic-btn {
  padding: 0.625rem 1.25rem;
  font-size: 0.8125rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border: 2px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text);
  min-height: 44px;
  min-width: 44px;
}

.heuristic-btn.active {
  background: var(--color-text);
  color: var(--color-bg);
  border-color: var(--color-text);
}
```

### 7.9 View Toggle (Segmented Control)

From [`backtest.vue`](frontend/pages/backtest.vue:554):

```css
.view-toggle {
  display: flex;
  gap: 0.375rem;
  background: var(--color-bg);
  border: 2px solid var(--color-text);
  border-radius: 0.5rem;
  padding: 0.25rem;
}

.toggle-btn.active {
  background: var(--color-text);
  color: var(--color-bg);
}
```

### 7.10 Data Table

From [`backtest.vue`](frontend/pages/backtest.vue:711):

```css
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.data-table thead {
  background: var(--color-border);
}

.data-table th {
  padding: 0.625rem 0.875rem;
  text-align: left;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 0.6875rem;
}
```

### 7.11 Status Badges

From [`game/[slug].vue`](frontend/pages/game/[slug].vue:269):

```css
.status {
  padding: 0.25rem 0.75rem;
  border-radius: 9999px;          /* pill shape */
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  background: var(--color-muted);  /* default grey */
  color: white;
}

.status.completed {
  background: #10b981;             /* emerald green */
}
```

### 7.12 Round Display

From [`index.vue`](frontend/pages/index.vue:282):

```css
.round-display {
  display: flex;
  align-items: centre;
  justify-content: centre;
  gap: 0.75rem;
  padding: 1rem 1.5rem;
  border: 1px solid var(--color-border);
}
```

### 7.13 Current Season Card

From [`backtest.vue`](frontend/pages/backtest.vue:468):

```css
.current-season-card {
  background: var(--color-bg);
  border: 2px solid var(--color-text);    /* thicker border */
  border-radius: 0.75rem;                 /* slightly rounded */
  padding: 1.25rem;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}
```

> This is the **only component** that uses `box-shadow` at rest and `border-radius: 0.75rem`.

### 7.14 Error Page

**File:** [`error.vue`](frontend/error.vue:1)

| Property | Value |
|---|---|
| Layout | Centre-aligned, `min-height: 60vh`, flexbox centre |
| Status code | `4rem` font size |
| Back button | Black bg, white text, `border-radius: 4px`, hover darkens to `#333` |

---

## 8. Interactive Patterns

### 8.1 Card Hover States

| Component | Rest State | Hover State |
|---|---|---|
| **TipCard** | `border: 1px solid var(--color-border)` | `border-color: var(--color-text)` |
| **Game card link** | — | `transform: translateY(-2px)`, `box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1)` |
| **Model card** | `border: 1px solid var(--color-border)` | `border-color: var(--color-text)` |
| **MatchAnalysisCard** | `border: 1px solid var(--color-border)` + amber left | Entire border goes `#f59e0b` |
| **Button** | Black border, white bg | Inverts to black bg, white text |
| **Buy Me a Coffee** | Yellow bg | Darkens yellow, lifts `-1px`, adds shadow |

### 8.2 Transition Timing

All transitions use `0.2s ease`:

```css
transition: all 0.2s ease;
transition: border-color 0.2s ease;
transition: color 0.2s ease;
transition: opacity 0.2s ease;
```

### 8.3 Active Button Press

```css
.btn:active {
  transform: scale(0.98);
}
```

### 8.4 Link Hover

```css
a:hover {
  opacity: 0.7;
}
```

### 8.5 Loading Spinner

```css
.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-text);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
```

---

## 9. Charts & Data Visualisation

### 9.1 Chart Library

Chart.js 4.x via vue-chartjs 5.x. Registered components:

- **Bar chart**: `CategoryScale`, `LinearScale`, `BarElement` (Accuracy)
- **Line chart**: `PointElement`, `LineElement` (Profit, Cumulative Profit)
- **Fill**: `Filler` plugin (Cumulative Profit area fill)
- **Shared**: `Title`, `Tooltip`, `Legend`

### 9.2 Chart Options (Shared)

```typescript
{
  responsive: true,
  maintainAspectRatio: false,
  interaction: {
    mode: 'index',
    intersect: false
  },
  plugins: {
    legend: {
      position: 'top',
      labels: {
        usePointStyle: true,
        padding: 20,
        font: { size: 12, weight: 600 }
      }
    },
    tooltip: {
      backgroundColor: 'rgba(0, 0, 0, 0.8)',
      padding: 12,
      titleFont: { size: 14, weight: 700 },
      bodyFont: { size: 13 }
    }
  },
  scales: {
    x: {
      grid: { display: false },
      title: { font: { size: 12, weight: 600 } }
    },
    y: {
      grid: { color: 'rgba(0, 0, 0, 0.05)' },
      ticks: { /* context-specific */ }
    }
  }
}
```

### 9.3 Chart Colour Application

- **Bar charts**: `backgroundColor` at `0.8` opacity, `borderWidth: 2`, `borderRadius: 4`
- **Line charts**: `borderColor` for line, `backgroundColor` at `0.1` opacity for fill, `tension: 0.3`
- **Cumulative line**: `fill: true`, `tension: 0.4`, `pointBorderWidth: 2`, `pointBorderColor: '#fff'`

### 9.4 Chart Layout

```css
.charts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
  gap: 1.5rem;
}

.charts-full-width {
  width: 100%;
}
```

---

## 10. Page Templates

### 10.1 Homepage (`/`)

**Sections:**
1. **Hero** — Full-width, centre-aligned, oversized h1 with `<br>` line break, subtitle paragraph
2. **Round Display** — Centre-aligned flex row with round label, value, game count
3. **Data Warning** (conditional) — Amber-tinted info box
4. **Heuristic Selector** — Centre-aligned button group (Best Bet / YOLO / High Risk)
5. **Games Grid** — Auto-fit responsive grid of game+tip cards
6. **Generate Tips Bar** — Dashed-border bar with CTA button

### 10.2 Game Detail (`/game/[slug]`)

**Sections:**
1. **Back Link** — "← Back to Tips"
2. **Game Header** — Round/season/status badges, team logos + names + scores, venue/date/time meta grid
3. **Heuristic Tips** — Grid of TipCards with heuristic-specific left-border colours
4. **Model Predictions** — Grid of model prediction cards
5. **Match Analysis** — MatchAnalysisCard (conditional)

### 10.3 Backtest (`/backtest`)

**Sections:**
1. **Hero** — Title + subtitle
2. **Current Season** — Blue gradient background, grid of stat cards with profit/accuracy
3. **Performance Comparison** — Season selector dropdown + segmented view toggle (Summary / Table / Charts)
4. **Summary View** — Grid of stat cards per heuristic
5. **Table View** — Per-heuristic tables with round-by-round data
6. **Charts View** — ProfitChart + AccuracyChart (side-by-side) + CumulativeProfitChart (full-width)

### 10.4 About (`/about`)

**Sections:**
1. **Hero** — Title + subtitle
2. **Our Approach** — Text content
3. **Models** — Vertical stack of model cards (Elo, Form, Home Advantage, Value)
4. **Heuristics** — Vertical stack of heuristic cards (Best Bet, YOLO, High Risk)
5. **Data Source** — Text content
6. **Open Source** — Text content

> Content pages use `max-width: 80ch` for optimal reading width.

---

## 11. Accessibility

### 11.1 Skip Link

From [`default.vue`](frontend/layouts/default.vue:3):

```html
<a href="#main-content" class="skip-link">Skip to main content</a>
```

Positioned off-screen (`top: -40px`), slides in on focus.

### 11.2 ARIA Attributes

| Pattern | Implementation |
|---|---|
| Heuristic selector | `role="tablist"`, `role="tab"`, `aria-selected` |
| Loading states | `role="status"`, `aria-live="polite"` |
| Nav logo | `aria-label="WhatIsMyTip home"` |
| Team logos | `:alt="teamName + ' logo'"` |
| Season selector | `aria-label="Select season year"` |

### 11.3 Touch Targets

All interactive elements have a minimum size of `44px`:

```css
min-height: 44px;
min-width: 44px;
```

### 11.4 Semantic HTML

- `<header>`, `<nav>`, `<main>`, `<footer>` landmark elements
- `<section>` for content groupings
- `<table>` with `<thead>` / `<tbody>` for tabular data
- `<ul>` / `<li>` for navigation lists

---

## 12. Animation & Transitions

### 12.1 Vue Transitions

```css
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.1s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0.5;
}
```

Used with `<Transition name="fade" mode="out-in">` for heuristic switching on the homepage.

### 12.2 CSS Animations

| Animation | Duration | Timing | Usage |
|---|---|---|---|
| `spin` | `0.8s` | `linear infinite` | Loading spinners |
| `spin` (small) | `0.6s` | `linear infinite` | Inline generating spinner |
| `spin` (chart) | `1s` | `linear infinite` | Chart loading spinners |

### 12.3 Hover Transitions

All hover effects transition over `0.2s ease`.

---

## 13. Iconography & Imagery

### 13.1 Icons

- **No icon library** is used
- Emoji characters for decorative accents: `🗣️` (talking points), `🏆` (current season), `⚠️` (disclaimer)
- Inline SVG for the Buy Me a Coffee coffee cup icon

### 13.2 Team Logos

- Stored as PNG files in [`public/logos/`](frontend/public/logos/)
- Rendered with `object-fit: contain` at responsive sizes (40–96 px)
- All images use `loading="lazy"` and explicit `width`/`height` attributes

---

## 14. SEO & Meta Patterns

### 14.1 Head Configuration

Each page uses Nuxt's [`useHead()`](frontend/pages/index.vue:115) composable for:

- **`title`** + `titleTemplate: '%s | WhatIsMyTip'` (set in nuxt.config)
- **`meta description`** — unique per page
- **`meta keywords`** — unique per page
- **`og:*`** properties — title, description, url
- **`twitter:*`** properties — card, title, description

### 14.2 Structured Data (JSON-LD)

Injected via `<script type="application/ld+json">` in:

- **nuxt.config.ts** — `WebSite` and `Organization` schemas (global)
- **index.vue** — `WebPage` + `SportsEvent` schema
- **about.vue** — `AboutPage` + `SoftwareApplication` schema
- **backtest.vue** — `WebPage` + `Dataset` schema

### 14.3 Robots & Sitemap

- [`public/robots.txt`](frontend/public/robots.txt) — included
- [`public/sitemap.xml`](frontend/public/sitemap.xml) — included
- Canonical URL set in nuxt.config

---

## 15. File & Folder Conventions

```
frontend/
├── app.vue                    # Root app wrapper (flex column, min-height 100vh)
├── error.vue                  # Global error page
├── nuxt.config.ts             # Nuxt configuration (head, modules, runtime config)
├── package.json               # Dependencies & scripts
├── assets/
│   └── css/
│       └── main.css           # Global styles, design tokens, base typography
├── components/
│   ├── Header.vue             # Sticky nav bar
│   ├── Footer.vue             # Site footer with links
│   ├── GameCard.vue           # Match summary card
│   ├── TipCard.vue            # Heuristic tip card
│   ├── MatchAnalysisCard.vue  # BBQ talking points card
│   ├── AccuracyChart.vue      # Bar chart (accuracy per round)
│   ├── ProfitChart.vue        # Line chart (profit per round)
│   └── CumulativeProfitChart.vue # Area chart (cumulative profit)
├── composables/
│   ├── useApi.ts              # API client with timeout, all endpoints
│   ├── useChartTheme.ts       # Chart colour/label mappings
│   ├── useFormatters.ts       # Date, heuristic, model name formatters
│   └── useTeamLogos.ts        # Team name → logo URL mapping
├── layouts/
│   └── default.vue            # Skip link + Header + main + Footer
├── pages/
│   ├── index.vue              # Homepage (tips)
│   ├── about.vue              # About page
│   ├── backtest.vue           # Backtesting dashboard
│   └── game/
│       └── [slug].vue         # Game detail page
├── public/
│   ├── robots.txt
│   ├── sitemap.xml
│   └── logos/                 # 18 AFL team logo PNGs
└── tests/
    └── game-detail-flow.spec.ts  # Playwright E2E test
```

### Styling Conventions

- **Scoped CSS** in every component (`<style scoped>`)
- **CSS custom properties** from `main.css` for shared tokens
- **No Tailwind utility classes** in templates (Tailwind provides reset + forms plugin only)
- **Mobile-first** responsive design with 3 explicit breakpoint blocks
- **No CSS preprocessor** (plain CSS with custom properties)

---

## 16. Replication Checklist

Use this checklist to recreate the WhatIsMyTip aesthetic on a new project:

### Setup

- [ ] Install Nuxt 4 + Vue 3 + TypeScript
- [ ] Add `@nuxtjs/tailwindcss` and `@tailwindcss/forms`
- [ ] Add `chart.js` + `vue-chartjs` (if charts needed)
- [ ] Create `assets/css/main.css` with the 5 CSS custom properties
- [ ] Add system font stack to `body`
- [ ] Set up font smoothing (`antialiased` / `grayscale`)

### Design Tokens

- [ ] Define `--color-bg`, `--color-text`, `--color-border`, `--color-muted`, `--color-hover`
- [ ] Add semantic colours: positive (`#00a000`), negative (`#c00000`), warning (`#f59e0b`), success (`#10b981`)
- [ ] Add data series colours: blue, orange, red, violet

### Typography

- [ ] Set heading weights to `800` with `-0.02em` letter-spacing
- [ ] Use `clamp()` for all heading sizes (fluid type)
- [ ] Set body text to `1.125rem` with `1.7` line-height
- [ ] Use `uppercase` + `0.05em`–`0.1em` letter-spacing for labels and badges

### Layout

- [ ] Set `max-width: 1400px` for nav/footer
- [ ] Set `max-width: 80ch` for reading pages
- [ ] Use 3-breakpoint system: `640px`, `1024px`
- [ ] Use `repeat(auto-fit, minmax(Xpx, 1fr))` for all grids

### Components

- [ ] Cards: `1px solid var(--color-border)` border, no border-radius, no shadow
- [ ] Card hover: `border-color: var(--color-text)` with `0.2s ease` transition
- [ ] Buttons: `2px solid` border, uppercase, `0.05em` letter-spacing, invert on hover
- [ ] Loading spinners: `40px` circle, `3px` border, `0.8s linear infinite`
- [ ] Status badges: pill shape (`border-radius: 9999px`), `0.75rem`, uppercase

### Accessibility

- [ ] Add skip link to layout
- [ ] Use `role` and `aria-*` attributes on interactive elements
- [ ] Set `min-height: 44px` / `min-width: 44px` on touch targets
- [ ] Use semantic HTML landmarks

### SEO

- [ ] Configure `titleTemplate` in nuxt.config
- [ ] Add `useHead()` with unique meta per page
- [ ] Add JSON-LD structured data per page
- [ ] Add `robots.txt` and `sitemap.xml`

### Charts (if applicable)

- [ ] Register Chart.js components
- [ ] Create `useChartTheme` composable with colour mappings
- [ ] Use shared chart options (dark tooltip, point-style legend, hidden x-grid)
- [ ] Wrap charts in bordered containers with `border-radius: 0.5rem`

---

*Document generated from source code analysis on 2026-04-27. All values extracted directly from component files.*
