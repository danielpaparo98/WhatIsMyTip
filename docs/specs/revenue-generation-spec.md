# Revenue Generation Specification

> **Spec ID**: REVGEN-001
> **Status**: Draft
> **Branch**: `feature/revenue-generation`
> **Author**: Strategy Advisor
> **Created**: 2026-05-17
> **Last Updated**: 2026-05-17

---

## Table of Contents

- [Revenue Generation Specification](#revenue-generation-specification)
  - [Table of Contents](#table-of-contents)
  - [1. Executive Summary](#1-executive-summary)
    - [Core Value Propositions](#core-value-propositions)
    - [Multi-Sport Expansion](#multi-sport-expansion)
  - [2. Goals \& Success Metrics](#2-goals--success-metrics)
    - [Business Goals](#business-goals)
    - [Success Metrics](#success-metrics)
    - [Technical Goals](#technical-goals)
  - [3. Revenue Model](#3-revenue-model)
    - [Pricing Structure](#pricing-structure)
    - [Why This Works](#why-this-works)
    - [Revenue Projections (Conservative)](#revenue-projections-conservative)
    - [Future Revenue Streams (Not in V1)](#future-revenue-streams-not-in-v1)
  - [4. User Tiers \& Feature Matrix](#4-user-tiers--feature-matrix)
    - [Feature Access by Tier](#feature-access-by-tier)
    - [Important Notes](#important-notes)
  - [5. User Stories](#5-user-stories)
    - [Epic 1: User Authentication](#epic-1-user-authentication)
      - [US-1.1: Register Account](#us-11-register-account)
      - [US-1.2: Login](#us-12-login)
      - [US-1.3: Password Reset](#us-13-password-reset)
    - [Epic 2: Competition Management](#epic-2-competition-management)
      - [US-2.1: Create Competition](#us-21-create-competition)
      - [US-2.2: Join Competition](#us-22-join-competition)
      - [US-2.3: Configure Competition Rules](#us-23-configure-competition-rules)
      - [US-2.4: View Leaderboard](#us-24-view-leaderboard)
      - [US-2.5: Submit Tips](#us-25-submit-tips)
      - [US-2.6: Competition Chat](#us-26-competition-chat)
    - [Epic 3: Prize Pool Settlement](#epic-3-prize-pool-settlement)
      - [US-3.1: Configure Prize Pool](#us-31-configure-prize-pool)
      - [US-3.2: Generate Settlement Report](#us-32-generate-settlement-report)
      - [US-3.3: Payment Link Templates](#us-33-payment-link-templates)
    - [Epic 4: Multi-Sport Support](#epic-4-multi-sport-support)
      - [US-4.1: Sport Selection](#us-41-sport-selection)
      - [US-4.2: Cross-Sport Dashboard](#us-42-cross-sport-dashboard)
  - [6. Feature Specifications](#6-feature-specifications)
    - [6.1 User Registration \& Authentication](#61-user-registration--authentication)
    - [6.2 Competition System](#62-competition-system)
    - [6.3 User Tips](#63-user-tips)
    - [6.4 Prize Pool Settlement](#64-prize-pool-settlement)
    - [6.5 Payment Processing](#65-payment-processing)
  - [7. Database Schema](#7-database-schema)
    - [7.1 New Tables](#71-new-tables)
      - [`users`](#users)
      - [`competitions`](#competitions)
      - [`competition_members`](#competition_members)
      - [`user_tips`](#user_tips)
      - [`purchases`](#purchases)
      - [`prize_pools`](#prize_pools)
      - [`settlements`](#settlements)
      - [`competition_messages`](#competition_messages)
    - [7.2 Existing Table Modifications](#72-existing-table-modifications)
      - [`games` table additions](#games-table-additions)
  - [8. API Design](#8-api-design)
    - [8.1 Authentication Endpoints](#81-authentication-endpoints)
    - [8.2 Competition Endpoints](#82-competition-endpoints)
    - [8.3 User Tips Endpoints](#83-user-tips-endpoints)
    - [8.4 Settlement Endpoints](#84-settlement-endpoints)
    - [8.5 Payment Endpoints](#85-payment-endpoints)
    - [8.6 Chat Endpoints](#86-chat-endpoints)
    - [8.7 Multi-Sport Endpoints (Modifications)](#87-multi-sport-endpoints-modifications)
  - [9. Settlement Algorithm](#9-settlement-algorithm)
    - [Problem Statement](#problem-statement)
    - [Algorithm: Greedy Settlement with Subgroup Optimization](#algorithm-greedy-settlement-with-subgroup-optimization)
    - [Implementation Notes](#implementation-notes)
    - [Pseudocode](#pseudocode)
  - [10. Technical Architecture](#10-technical-architecture)
    - [10.1 System Architecture Overview](#101-system-architecture-overview)
    - [10.2 Backend Module Structure (New Files)](#102-backend-module-structure-new-files)
    - [10.3 Frontend Structure (New Files)](#103-frontend-structure-new-files)
    - [10.4 Key Design Decisions](#104-key-design-decisions)

---

## 1. Executive Summary

WhatIsMyTip is an AI-powered AFL tipping platform that currently offers free ML-based tips, AI explanations, and backtesting. This specification defines the transformation from a free-only platform into a revenue-generating SaaS product while keeping the core tipping experience free.

The revenue model centres on **social tipping competitions** — users pay per competition per season to host and manage tipping comps with friends, family, or colleagues. A premium tier adds a **prize pool settlement calculator** that tells participants exactly how to split prize money at season end.

**Key principle: The platform never touches prize money.** It is a calculator (like Splitwise), not a financial intermediary. This keeps the product legally classified as SaaS, not gambling, under Australian law.

### Core Value Propositions

| Tier | Value Proposition | Price |
|------|-------------------|-------|
| **Free** | AI tips for all supported sports, basic game info, backtest results | $0 |
| **Comp Host** | Create tipping comps, invite links, live leaderboard, scoring, chat | $5/season/comp |
| **Comp Host + Prize Split** | Everything in Comp Host + prize pool calculator, settlement report, payment link templates | $10/season/comp |

### Multi-Sport Expansion

To achieve year-round engagement, the platform will expand from AFL-only to support:

- **AFL** (March–September) — existing
- **NRL** (March–October)
- **BBL Cricket** (December–February)
- **A-League Soccer** (October–May)
- **Super Rugby** (February–July)

This ensures paying users have active competitions across most of the calendar year.

---

## 2. Goals & Success Metrics

### Business Goals

1. **Generate revenue** from Season 1 of launch (target: $500 MRR by month 6)
2. **Maintain free tier** as the primary traffic driver (SEO, word-of-mouth)
3. **Achieve 5% free-to-paid conversion** within first 3 months
4. **Zero legal exposure** — platform never touches money, clear ToS, responsible gambling disclaimers
5. **Year-round engagement** via multi-sport support

### Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Registered users | 500 in first 3 months | Database count |
| Competitions created | 100 in first season | Database count |
| Free-to-paid conversion | 5% | Paid users / registered users |
| Average comp size | 8 members | Avg members per competition |
| Season renewal rate | 60% | Repeat purchases season-over-season |
| Multi-sport adoption | 30% of paid users in 2+ sports | Cross-sport purchase rate |
| NPS score | > 40 | Post-season survey |

### Technical Goals

1. **Zero downtime** migration — existing free features continue working throughout rollout
2. **Sub-200ms API response** for competition endpoints (leaderboard, standings)
3. **99.5% uptime** during tipping deadline windows (Thursday–Friday peak)
4. **Full test coverage** for payment flows, settlement algorithm, and competition logic

---

## 3. Revenue Model

### Pricing Structure

The pricing is a **one-time purchase per competition per season**. There are no recurring subscriptions — users buy access for each comp they want to host, for each sport season.

| Product | Price | Billing | What You Get |
|---------|-------|---------|--------------|
| Free Tips | $0 | Forever | AI tips (Best Bet heuristic), game info, backtest results for all sports |
| Comp Host | $5 AUD | Per comp, per season | Create comp, invite mates, live leaderboard, automated scoring, chat |
| Comp Host + Prize Split | $10 AUD | Per comp, per season | Everything in Comp Host + prize pool calculator, settlement report, payment templates |

### Why This Works

1. **Impulse purchase**: $10/comp is cheaper than a pint at the footy. A user collecting $200–400 from mates won't blink at $10.
2. **No subscription friction**: One-time purchase removes churn anxiety. Users pay when they need it.
3. **Viral coefficient**: Each paid comp brings 5–15 free users who may become paid hosts themselves.
4. **Multi-sport multiplier**: A user hosting AFL + NRL + BBL comps = 3 purchases per year.

### Revenue Projections (Conservative)

| Scenario | Comps/Season | Avg Price | Revenue/Season |
|----------|-------------|-----------|----------------|
| Month 1–3 | 20 | $7.50 | $150 |
| Month 4–6 | 80 | $7.50 | $600 |
| Month 7–12 | 200 | $7.50 | $1,500 |
| Year 2 (multi-sport) | 500 | $7.50 | $3,750 |

### Future Revenue Streams (Not in V1)

- **Betting affiliate CPAs**: Links to Sportsbet, Ladbrokes, TAB on game pages ($50–150 CPA)
- **B2B venue licensing**: Pubs/RSLs running footy tipping comps ($50–100/month)
- **Premium AI insights**: Deeper analysis, custom heuristics, API access

---

## 4. User Tiers & Feature Matrix

### Feature Access by Tier

| Feature | Free | Comp Host ($5) | Comp Host + Prize Split ($10) |
|---------|------|----------------|-------------------------------|
| AI tips (Best Bet) | Yes | Yes | Yes |
| All heuristic tips | Yes | Yes | Yes |
| Game info & analysis | Yes | Yes | Yes |
| Backtest results | Yes | Yes | Yes |
| User account | Yes | Yes | Yes |
| Join a competition | Yes | Yes | Yes |
| Create a competition | No | Yes | Yes |
| Invite link generation | No | Yes | Yes |
| Live leaderboard | No | Yes (as member) | Yes (as member) |
| Automated scoring | No | Yes | Yes |
| Comp chat / banter | No | Yes | Yes |
| Round reminders | No | Yes | Yes |
| Prize pool setup | No | No | Yes |
| Settlement calculator | No | No | Yes |
| Settlement report (PDF) | No | No | Yes |
| Payment link templates | No | No | Yes |
| "Who owes whom" minimizer | No | No | Yes |

### Important Notes

- **Joining a comp is free** — only the host pays. This maximises viral spread.
- **Any registered user can join unlimited comps** — the payment is only for creating/hosting.
- **Prize split is per-comp** — a host who wants prize split for 2 comps pays $10 x 2 = $20.
- **Free users can view tips without an account** — account is only needed to join comps.

---

## 5. User Stories

### Epic 1: User Authentication

#### US-1.1: Register Account
**As a** visitor
**I want to** create an account with email and password
**So that** I can join tipping competitions and track my tips

**Acceptance Criteria:**
- Email verification required (send confirmation link)
- Password minimum 8 characters, at least 1 uppercase, 1 number
- Display name required (shown on leaderboards)
- Option to upload avatar (gravatar fallback)
- Rate limited to 3 registration attempts per IP per hour

#### US-1.2: Login
**As a** registered user
**I want to** log in with email and password
**So that** I can access my competitions and tips

**Acceptance Criteria:**
- JWT token issued on successful login (7-day expiry)
- "Remember me" option extends to 30 days
- Failed login attempts rate-limited (5 per 15 minutes)
- Token stored in httpOnly cookie (not localStorage)

#### US-1.3: Password Reset
**As a** user who forgot their password
**I want to** reset it via email
**So that** I can regain access to my account

**Acceptance Criteria:**
- Reset link sent to registered email
- Link expires after 1 hour
- Rate limited to 3 reset requests per email per hour

### Epic 2: Competition Management

#### US-2.1: Create Competition
**As a** registered user
**I want to** create a tipping competition for a sport and season
**So that** I can invite my friends to compete against me

**Acceptance Criteria:**
- Must select sport (AFL, NRL, BBL, A-League, Super Rugby)
- Must select season (auto-populated from available data)
- Competition name required (max 50 chars)
- Optional: description, avatar/image
- Unique invite code generated automatically (8-char alphanumeric)
- Unique invite link generated (e.g., `whatismytip.com/join/ABC12345`)
- Payment required before competition is activated
- Max 50 members per competition

#### US-2.2: Join Competition
**As a** registered user
**I want to** join a competition via invite link or code
**So that** I can participate in the tipping comp

**Acceptance Criteria:**
- Invite link redirects to join page (login if not authenticated)
- Invite code can be entered manually on competitions page
- User is added to competition immediately upon joining
- Host receives notification when new member joins
- Cannot join same competition twice
- Competition member list visible to all members

#### US-2.3: Configure Competition Rules
**As a** competition host
**I want to** configure scoring rules and settings
**So that** the competition works the way my group wants

**Acceptance Criteria:**
- Scoring type: standard (1pt per correct tip) or margin (bonus for closeness)
- Tip deadline: first bounce of first game, or fixed time (e.g., Thursday 6pm)
- Late tip policy: auto-tip using AI Best Bet, or score 0
- Tiebreaker: total margin across season (cumulative error)
- Settings editable until first round begins

#### US-2.4: View Leaderboard
**As a** competition member
**I want to** see a live leaderboard showing all members ranked
**So that** I know where I stand and can talk trash

**Acceptance Criteria:**
- Sorted by total points (descending)
- Shows: rank, avatar, display name, points, correct tips count, margin (if applicable)
- Highlights current user's row
- Updates after each game result is processed
- Previous round movement indicator (up/down/same)
- "Round X" filter to see round-by-round standings

#### US-2.5: Submit Tips
**As a** competition member
**I want to** submit my tips for each round
**So that** I can compete against other members

**Acceptance Criteria:**
- Shows all games for the upcoming round
- Select winner for each game (radio button or tap)
- Optional: submit margin prediction (if margin scoring enabled)
- "Use AI Tips" button to auto-fill with Best Bet recommendations
- Save draft tips before deadline
- Tips lock at configured deadline time
- Cannot modify tips after deadline
- Visual indicator of locked vs unlocked rounds

#### US-2.6: Competition Chat
**As a** competition member
**I want to** post messages in a comp chat feed
**So that** I can banter with other members

**Acceptance Criteria:**
- Simple text chat (max 280 chars per message)
- Messages show avatar, name, timestamp
- Host can delete any message; users can delete their own
- Rate limited to 10 messages per minute
- Messages sorted newest-first (infinite scroll up)
- No images, links only (auto-linked, no embeds)

### Epic 3: Prize Pool Settlement

#### US-3.1: Configure Prize Pool
**As a** competition host (Prize Split tier)
**I want to** define the prize pool structure
**So that** the settlement calculator knows how to distribute winnings

**Acceptance Criteria:**
- Entry fee per member (e.g., $20 each)
- Payout structure: predefined templates or custom
  - Winner-takes-all: 100% to 1st
  - Top 3: 60% / 30% / 10%
  - Top 5: 50% / 25% / 15% / 7% / 3%
  - Custom: drag sliders to allocate percentages
- Wooden spoon bonus: optional small payout to last place (e.g., $10 back)
- Total prize pool calculated and displayed in real-time
- Can be configured/changed until first round begins

#### US-3.2: Generate Settlement Report
**As a** competition host (Prize Split tier)
**I want to** generate a settlement report at season end
**So that** I know exactly who owes whom how much

**Acceptance Criteria:**
- Calculates final standings based on all round results
- Applies payout structure to determine winnings per member
- Runs transaction minimization algorithm (see Section 9)
- Produces a clear "who pays whom" list
- Shows: total pool, each person's net position (owes/receives), minimum transactions
- One-click copy of settlement details
- Shareable link to settlement page (members can view)

#### US-3.3: Payment Link Templates
**As a** competition host (Prize Split tier)
**I want to** send payment links to members who owe money
**So that** settling up is easy

**Acceptance Criteria:**
- Generate Beem It / PayID / bank transfer templates
- Pre-filled with amount and description
- Host enters their PayID/Bank details once
- "Send reminder" button generates a copyable message with payment details
- Platform does NOT process payments — just generates the instructions
- Clear disclaimer: "WhatIsMyTip does not handle money. Settle directly with your comp host."

### Epic 4: Multi-Sport Support

#### US-4.1: Sport Selection
**As a** user
**I want to** switch between sports to see tips and games
**So that** I can follow multiple sports throughout the year

**Acceptance Criteria:**
- Sport selector in header/nav (tabs or dropdown)
- Default sport based on current season (e.g., AFL in April, BBL in January)
- Each sport has its own fixtures, tips, and leaderboard
- URL structure: `whatismytip.com/{sport}/round/{n}`
- Sport-specific branding (colours, logos)

#### US-4.2: Cross-Sport Dashboard
**As a** registered user
**I want to** see all my active competitions across sports
**So that** I have a single view of everything

**Acceptance Criteria:**
- Dashboard page showing all joined competitions
- Sport icon, comp name, current rank, upcoming deadline
- Quick links to submit tips for each comp
- Notification badges for unsubmitted tips

---

## 6. Feature Specifications

### 6.1 User Registration & Authentication

**Backend Changes:**

- New `User` model (see Section 7 for schema)
- New `auth.py` service with bcrypt password hashing
- New `auth.py` API router with `/register`, `/login`, `/logout`, `/reset-password` endpoints
- JWT tokens stored in httpOnly cookies
- Email verification via sendgrid/mailgun (or similar transactional email service)
- Middleware to extract current user from JWT on authenticated routes

**Frontend Changes:**

- New `/register` and `/login` pages
- Auth composable (`useAuth.ts`) for reactive user state
- Protected route middleware
- Header updates: show user avatar/name when logged in, login/register buttons when not

### 6.2 Competition System

**Backend Changes:**

- New models: `Competition`, `CompetitionMember`, `CompetitionSettings`
- New `competitions.py` API router with CRUD + invite + scoring endpoints
- New `competition_service.py` for business logic
- Scoring engine: processes game results and updates member scores
- Invite code generation: 8-char alphanumeric, unique index
- Cron job enhancement: after `match_completion` job runs, trigger scoring update for all active competitions

**Frontend Changes:**

- New `/competitions` page (list user's comps + join via code)
- New `/competitions/[id]` page (comp detail with leaderboard, chat, settings)
- New `/competitions/[id]/tips` page (submit tips for current round)
- New `/competitions/create` page (create comp wizard with payment)
- New `/join/[code]` page (join via invite link)
- Competition components: Leaderboard, ChatFeed, TipSheet, CompSettings

### 6.3 User Tips

**Backend Changes:**

- New `UserTip` model linking user, competition, game, and predicted winner
- New `user_tips.py` CRUD module
- Validation: check deadline before allowing tip submission
- Batch endpoint: submit all tips for a round in one request
- "Use AI" endpoint: auto-fill tips from Best Bet heuristic for the user

**Frontend Changes:**

- Tip submission card per game (radio buttons for team selection)
- "Auto-fill with AI" button
- Visual lock indicator when deadline has passed
- Summary view showing submitted vs pending tips

### 6.4 Prize Pool Settlement

**Backend Changes:**

- New `PrizePool` and `Settlement` models
- New `settlement_service.py` with transaction minimization algorithm
- New `settlement.py` API router
- PDF generation for settlement report (using `reportlab` or `weasyprint`)
- Payment template generation (PayID, Beem It, bank transfer strings)

**Frontend Changes:**

- Prize pool configuration in comp settings
- Settlement page with visual "who owes whom" graph
- "Copy payment details" buttons
- "Share settlement" link generation

### 6.5 Payment Processing

**Backend Changes:**

- Stripe Checkout Session creation for one-time payments
- Webhook handler for `checkout.session.completed`
- Purchase record creation on successful payment
- Purchase validation middleware (check user has paid for comp before allowing host actions)

**Frontend Changes:**

- Stripe Checkout redirect on comp creation
- Payment success/cancel callback pages
- "My Purchases" page showing active purchases

---

## 7. Database Schema

### 7.1 New Tables

#### `users`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, auto-increment | User ID |
| `email` | String(255) | UNIQUE, NOT NULL | Login email |
| `password_hash` | String(255) | NOT NULL | bcrypt hash |
| `display_name` | String(100) | NOT NULL | Shown on leaderboards |
| `avatar_url` | String(500) | NULLABLE | Uploaded or gravatar URL |
| `email_verified` | Boolean | DEFAULT FALSE | Email verification status |
| `verification_token` | String(255) | NULLABLE | Email verification token |
| `reset_token` | String(255) | NULLABLE | Password reset token |
| `reset_token_expires` | DateTime | NULLABLE | Reset token expiry |
| `created_at` | DateTime | DEFAULT NOW | Registration date |
| `updated_at` | DateTime | DEFAULT NOW | Last update |

#### `competitions`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, auto-increment | Competition ID |
| `name` | String(50) | NOT NULL | Comp name |
| `description` | Text | NULLABLE | Comp description |
| `sport` | String(20) | NOT NULL | AFL, NRL, BBL, ALEAGUE, SUPERUGBY |
| `season` | Integer | NOT NULL | e.g., 2026 |
| `invite_code` | String(8) | UNIQUE, NOT NULL | Join code |
| `host_user_id` | Integer | FK users.id, NOT NULL | Comp creator |
| `max_members` | Integer | DEFAULT 50 | Member cap |
| `is_active` | Boolean | DEFAULT TRUE | Active flag |
| `scoring_type` | String(20) | DEFAULT 'standard' | 'standard' or 'margin' |
| `tip_deadline_type` | String(20) | DEFAULT 'first_bounce' | 'first_bounce' or 'fixed_time' |
| `tip_deadline_offset_minutes` | Integer | DEFAULT 0 | Minutes before first bounce |
| `late_tip_policy` | String(20) | DEFAULT 'auto_ai' | 'auto_ai' or 'zero' |
| `prize_pool_enabled` | Boolean | DEFAULT FALSE | Has prize pool? |
| `created_at` | DateTime | DEFAULT NOW | Creation date |
| `updated_at` | DateTime | DEFAULT NOW | Last update |

**Indexes:** `(sport, season)`, `(host_user_id)`, `(invite_code)`

#### `competition_members`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, auto-increment | Membership ID |
| `competition_id` | Integer | FK competitions.id, NOT NULL | Comp reference |
| `user_id` | Integer | FK users.id, NOT NULL | User reference |
| `joined_at` | DateTime | DEFAULT NOW | When they joined |
| `total_score` | Integer | DEFAULT 0 | Cumulative score |
| `correct_tips` | Integer | DEFAULT 0 | Total correct tips |
| `total_margin` | Float | DEFAULT 0.0 | Cumulative margin error |
| `last_round_rank` | Integer | NULLABLE | Previous round rank |

**Unique Constraint:** `(competition_id, user_id)`
**Indexes:** `(competition_id)`, `(user_id)`, `(competition_id, total_score DESC)`

#### `user_tips`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, auto-increment | Tip ID |
| `competition_id` | Integer | FK competitions.id, NOT NULL | Comp reference |
| `user_id` | Integer | FK users.id, NOT NULL | User reference |
| `game_id` | Integer | FK games.id, NOT NULL | Game reference |
| `predicted_winner` | String(100) | NOT NULL | Team name |
| `predicted_margin` | Integer | NULLABLE | Predicted margin (if margin scoring) |
| `is_auto_filled` | Boolean | DEFAULT FALSE | AI auto-filled? |
| `submitted_at` | DateTime | DEFAULT NOW | When submitted |

**Unique Constraint:** `(competition_id, user_id, game_id)`
**Indexes:** `(competition_id, game_id)`, `(user_id)`

#### `purchases`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, auto-increment | Purchase ID |
| `user_id` | Integer | FK users.id, NOT NULL | Buyer |
| `competition_id` | Integer | FK competitions.id, NOT NULL | Comp being paid for |
| `tier` | String(20) | NOT NULL | 'comp_host' or 'comp_host_prize' |
| `amount_cents` | Integer | NOT NULL | Price in cents (500 or 1000) |
| `currency` | String(3) | DEFAULT 'aud' | Currency code |
| `stripe_session_id` | String(255) | UNIQUE, NOT NULL | Stripe Checkout Session ID |
| `stripe_payment_intent` | String(255) | NULLABLE | Stripe PaymentIntent ID |
| `status` | String(20) | DEFAULT 'pending' | 'pending', 'completed', 'refunded' |
| `created_at` | DateTime | DEFAULT NOW | Purchase date |
| `completed_at` | DateTime | NULLABLE | When payment confirmed |

**Indexes:** `(user_id)`, `(competition_id)`, `(stripe_session_id)`

#### `prize_pools`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, auto-increment | Pool ID |
| `competition_id` | Integer | FK competitions.id, UNIQUE, NOT NULL | One pool per comp |
| `entry_fee_cents` | Integer | NOT NULL | Per-member entry fee |
| `payout_structure` | JSON | NOT NULL | e.g., {"1": 60, "2": 30, "3": 10} |
| `wooden_spoon_amount_cents` | Integer | DEFAULT 0 | Last place consolation |
| `host_pay_id` | String(100) | NULLABLE | Host's PayID for settlement |
| `host_bank_details` | Text | NULLABLE | Host's bank details (encrypted at rest) |

#### `settlements`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, auto-increment | Settlement ID |
| `competition_id` | Integer | FK competitions.id, NOT NULL | Comp reference |
| `transactions` | JSON | NOT NULL | Minimized transaction list |
| `total_pool_cents` | Integer | NOT NULL | Total prize pool |
| `generated_at` | DateTime | DEFAULT NOW | When generated |

#### `competition_messages`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | Integer | PK, auto-increment | Message ID |
| `competition_id` | Integer | FK competitions.id, NOT NULL | Comp reference |
| `user_id` | Integer | FK users.id, NOT NULL | Author |
| `message` | String(280) | NOT NULL | Message text |
| `created_at` | DateTime | DEFAULT NOW | Timestamp |
| `deleted_at` | DateTime | NULLABLE | Soft delete |

**Indexes:** `(competition_id, created_at DESC)`

### 7.2 Existing Table Modifications

#### `games` table additions

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `sport` | String(20) | DEFAULT 'afl', NOT NULL | Sport identifier |
| `external_id` | String(100) | NULLABLE | ID from external API (for dedup across sports) |

The existing `games` table needs a migration to add the `sport` column with default `'afl'` and `external_id` for multi-sport support.

---

## 8. API Design

### 8.1 Authentication Endpoints

```
POST   /api/auth/register          # Create account
POST   /api/auth/login             # Login (sets httpOnly cookie)
POST   /api/auth/logout            # Clear cookie
POST   /api/auth/reset-password    # Request reset email
POST   /api/auth/verify-email      # Verify email token
GET    /api/auth/me                # Get current user
```

### 8.2 Competition Endpoints

```
POST   /api/competitions                    # Create competition (requires payment)
GET    /api/competitions                    # List user's competitions
GET    /api/competitions/{id}               # Get competition detail
PUT    /api/competitions/{id}               # Update competition settings
DELETE /api/competitions/{id}               # Delete competition (before season start)
POST   /api/competitions/{id}/join          # Join via code (free)
POST   /api/competitions/{id}/leave         # Leave competition
GET    /api/competitions/{id}/members       # List members
GET    /api/competitions/{id}/leaderboard   # Get leaderboard
GET    /api/competitions/{id}/join-info     # Get join info from invite code
```

### 8.3 User Tips Endpoints

```
POST   /api/competitions/{id}/tips          # Submit tips for a round
GET    /api/competitions/{id}/tips          # Get user's tips for a round
GET    /api/competitions/{id}/tips/round/{n} # Get all members' tips (after deadline)
POST   /api/competitions/{id}/tips/auto-ai  # Auto-fill from AI
```

### 8.4 Settlement Endpoints

```
POST   /api/competitions/{id}/prize-pool    # Configure prize pool
GET    /api/competitions/{id}/prize-pool    # Get prize pool config
POST   /api/competitions/{id}/settlement    # Generate settlement report
GET    /api/competitions/{id}/settlement    # View settlement report
GET    /api/competitions/{id}/settlement/pdf # Download PDF report
POST   /api/competitions/{id}/settlement/remind # Generate payment reminder
```

### 8.5 Payment Endpoints

```
POST   /api/payments/checkout               # Create Stripe Checkout Session
GET    /api/payments/success                # Success callback
GET    /api/payments/cancel                 # Cancel callback
POST   /api/payments/webhook                # Stripe webhook
GET    /api/payments/history                # User's purchase history
```

### 8.6 Chat Endpoints

```
POST   /api/competitions/{id}/messages      # Post message
GET    /api/competitions/{id}/messages      # Get messages (paginated)
DELETE /api/competitions/{id}/messages/{mid} # Delete message
```

### 8.7 Multi-Sport Endpoints (Modifications)

```
GET    /api/{sport}/games-with-tips         # Sport-specific game list
GET    /api/{sport}/round/{n}               # Sport-specific round data
GET    /api/{sport}/leaderboard             # Sport-specific AI leaderboard
```

---

## 9. Settlement Algorithm

### Problem Statement

Given N competition members, each with a final rank and a corresponding payout, calculate the minimum number of transactions required to settle all debts. This is the classic "minimum transactions" or "optimal account balancing" problem.

### Algorithm: Greedy Settlement with Subgroup Optimization

```
1. Calculate net position for each member:
   net_position[i] = payout[i] - entry_fee

   Example (10 members, $20 entry, Top 3 split 60/30/10):
   - 1st place:  +$100  (wins $120, paid $20)
   - 2nd place:  +$40   (wins $60, paid $20)
   - 3rd place:  -$8    (wins $12, paid $20)
   - 4th-10th:   -$20 each (wins $0, paid $20)

2. Separate into creditors (positive) and debtors (negative):
   creditors = [+100, +40]
   debtors   = [-8, -20, -20, -20, -20, -20, -20, -20, -20]

3. Greedy matching:
   For each debtor, match with the creditor who can absorb the full amount.
   If creditor can't cover full amount, split the debtor's payment.

4. Result (minimum transactions):
   - Member 4 pays $20 to 1st place
   - Member 5 pays $20 to 1st place
   - Member 6 pays $20 to 1st place
   - Member 7 pays $20 to 1st place
   - Member 8 pays $20 to 1st place
   - Member 9 pays $8 to 2nd place + $12 to 1st place
   - Member 10 pays $20 to 2nd place
   - 3rd place pays $8 to 2nd place
   - 3rd place pays $12 to 1st place

   Total: 10 transactions (optimal for this distribution)
```

### Implementation Notes

- Use a priority queue (max-heap for creditors, min-heap for debtors)
- Time complexity: O(n log n) where n = number of members
- Handle edge cases:
  - Tied ranks: split the combined payout equally
  - Wooden spoon bonus: add to last place's payout before calculating
  - Partial season: prorate if comp ends early
  - Zero net positions: exclude from settlement (already even)

### Pseudocode

```python
def minimize_transactions(net_positions: dict[int, float]) -> list[Transaction]:
    """Given {user_id: net_amount}, return minimum transaction list."""
    creditors = []  # max-heap of (amount, user_id)
    debtors = []    # min-heap of (amount, user_id) - stored as negative

    for user_id, amount in net_positions.items():
        if amount > 0.01:
            heapq.heappush(creditors, (-amount, user_id))
        elif amount < -0.01:
            heapq.heappush(debtors, (amount, user_id))

    transactions = []
    while creditors and debtors:
        c_amount, c_id = heapq.heappop(creditors)
        c_amount = -c_amount  # negate back
        d_amount, d_id = heapq.heappop(debtors)

        transfer = min(c_amount, -d_amount)
        transactions.append(Transaction(
            from_user=d_id,
            to_user=c_id,
            amount=round(transfer, 2)
        ))

        remaining_creditor = c_amount - transfer
        remaining_debtor = d_amount + transfer

        if remaining_creditor > 0.01:
            heapq.heappush(creditors, (-remaining_creditor, c_id))
        if remaining_debtor < -0.01:
            heapq.heappush(debtors, (remaining_debtor, d_id))

    return transactions
```

---

## 10. Technical Architecture

### 10.1 System Architecture Overview

```
                    +------------------+
                    |   Nuxt 4 Frontend|
                    |   (SSR + SPA)    |
                    +--------+---------+
                             |
                    +--------v---------+
                    |   FastAPI Backend |
                    |   (Python 3.12+) |
                    +--------+---------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v--+   +------v------+  +----v-----+
     | SQLite/   |   | Stripe API  |  | OpenRouter|
     | PostgreSQL|   | (Payments)  |  | (AI)      |
     +-----------+   +-------------+  +-----------+
              |
     +--------v------------------------------------------+
     | External Data Sources (per sport)                  |
     | Squiggle (AFL) | SportRadar (NRL) | Cricket API   |
     +---------------------------------------------------+
```

### 10.2 Backend Module Structure (New Files)

```
backend/app/
  api/
    auth.py              # Authentication endpoints
    competitions.py      # Competition CRUD + management
    settlements.py       # Prize pool settlement endpoints
    payments.py          # Stripe checkout + webhooks
    chat.py              # Competition chat endpoints
    user_tips.py         # User tip submission endpoints
  crud/
    users.py             # User CRUD operations
    competitions.py      # Competition CRUD operations
    user_tips.py         # UserTip CRUD operations
    purchases.py         # Purchase CRUD operations
    settlements.py       # Settlement CRUD operations
    chat.py              # CompetitionMessage CRUD
  models/
    __init__.py          # Updated to include new models
    user.py              # User model
    competition.py       # Competition, CompetitionMember models
    user_tip.py          # UserTip model
    purchase.py          # Purchase model
    settlement.py        # PrizePool, Settlement models
    chat.py              # CompetitionMessage model
  schemas/
    auth.py              # Auth request/response schemas
    competitions.py      # Competition schemas
    user_tips.py         # User tip schemas
    payments.py          # Payment schemas
    settlements.py       # Settlement schemas
    chat.py              # Chat schemas
  services/
    auth_service.py      # Password hashing, JWT, email verification
    competition_service.py # Competition logic, scoring engine
    settlement_service.py  # Transaction minimization algorithm
    payment_service.py     # Stripe integration logic
    scoring_service.py     # Score calculation after game results
  middleware/
    auth.py              # JWT extraction middleware
```

### 10.3 Frontend Structure (New Files)

```
frontend/
  pages/
    register.vue              # Registration page
    login.vue                 # Login page
    dashboard.vue             # User dashboard (all comps)
    competitions/
      index.vue               # List competitions + join
      create.vue              # Create competition wizard
      [id].vue                # Competition detail (leaderboard, chat)
      [id]/
        tips.vue              # Submit tips for current round
        settings.vue          # Comp settings (host only)
        settlement.vue        # Prize pool settlement
    join/
      [code].vue              # Join via invite link
    payment/
      success.vue             # Payment success callback
      cancel.vue              # Payment cancelled callback
  components/
    auth/
      LoginForm.vue
      RegisterForm.vue
    competition/
      Leaderboard.vue
      ChatFeed.vue
      TipSheet.vue
      CompCard.vue
      InviteLink.vue
      CreateCompWizard.vue
    settlement/
      SettlementReport.vue
      PrizePoolConfig.vue
      PaymentTemplate.vue
      TransactionGraph.vue
  composables/
    useAuth.ts                # Auth state + methods
    useCompetitions.ts        # Competition data fetching
    useTips.ts                # Tip submission logic
    usePayments.ts            # Stripe checkout integration
  middleware/
    auth.ts                   # Route guard for authenticated pages
  server/
    api/
      auth.ts                 # Proxy auth calls to backend
      payments.ts             # Proxy payment calls to backend
```

### 10.4 Key Design Decisions

1. **SQLite for dev, PostgreSQL for prod** — existing pattern continues
2. **JWT in httpOnly cookies** — not localStorage, for XSS protection
3. **Stripe Checkout (not Elements)** — redirect-based, less PCI scope
4. **One-time payments (not subscriptions)** — per comp per season
5. **Settlement as pure calculator** — platform never touches money
6. **Sport as a column on games** — not separate tables, for query simplicity
7. **Scoring runs in match_completion cron** — existing job extended
8. **Chat is simple text** — no media, no real-time (polling every 30s for v1)

---

## 11. Multi-Sport Expansion

### 11.1 Sport Abstraction Layer

Each sport needs a data provider that implements a common interface:

```python
class SportDataProvider(ABC):
    @abstractmethod
    async def fetch_games(self, round_num: int, season: int) -> list[dict]:
        """Fetch games for a given round and season."""

    @abstractmethod
    async def fetch_results(self, round_num: int, season: int) -> list[dict]:
        """Fetch completed game results."""

    @abstractmethod
    async def get_teams(self) -> list[dict]:
        """Get all teams for this sport."""

    @abstractmethod
    async def get_current_round(self) -> int:
        """Get the current round number."""

    @abstractmethod
    def get_sport_name(self) -> str:
        """Return sport identifier (e.g., 'afl', 'nrl')."""
```

### 11.2 Data Sources per Sport

| Sport | Data Source | API | Cost |
|-------|------------|-----|------|
| AFL | Squiggle API | REST (existing) | Free |
| NRL | SportRadar API | REST | Free tier (1000 req/month) |
| BBL Cricket | Cricket Data API | REST | Free tier |
| A-League | Football Data API | REST | Free tier |
| Super Rugby | ESPN Cricinfo / Rugby API | REST | Free tier |

### 11.3 ML Model Adaptation

The existing ML models (Elo, Form, Home Advantage, Value) are sport-agnostic by design:

- **EloModel**: Works for any two-team sport. Needs sport-specific K-factor tuning.
- **FormModel**: Based on recent results. Transferable — just needs game data.
- **HomeAdvantageModel**: Statistical home-ground advantage. Varies by sport.
- **ValueModel**: Odds-based value detection. Requires odds data source per sport.

**V1 approach**: Apply existing models to new sports with default parameters. Tune per-sport after collecting 1 season of data.

### 11.4 Heuristic Adaptation

Existing heuristics (Best Bet, YOLO, High Risk High Reward) are model-agnostic — they operate on model predictions, not sport-specific data. They work for any sport without modification.

### 11.5 Cron Job Changes

Each sport needs its own data sync cron job:

```
cron/jobs/
  afl_daily_sync.py       # Existing
  nrl_daily_sync.py       # New
  bbl_daily_sync.py       # New
  aleague_daily_sync.py   # New
  super_rugby_daily.py    # New
```

The `match_completion` and `tip_generation` jobs need sport-awareness to process all active sports.

### 11.6 Frontend Routing

```
/{sport}/                  # Sport home (games + tips for current round)
/{sport}/round/{n}         # Specific round
/{sport}/game/{slug}       # Game detail
/{sport}/backtest          # Backtest results
```

Default route `/` redirects to the current in-season sport.

---

## 12. Authentication & Security

### 12.1 Authentication Flow

1. User registers with email + password + display name
2. Backend hashes password with bcrypt (cost factor 12)
3. Verification email sent with token (expires 24h)
4. User clicks link, email marked verified
5. User can now log in: POST `/api/auth/login`
6. Backend validates credentials, issues JWT (httpOnly cookie)
7. JWT payload: `{user_id, email, exp}`
8. All authenticated endpoints read JWT from cookie

### 12.2 JWT Configuration

```python
# In config.py
auth_secret_key: str = ""           # HS256 signing key
auth_token_expiry_hours: int = 168  # 7 days default
auth_remember_me_days: int = 30     # "Remember me" option
```

### 12.3 Security Measures

| Measure | Implementation |
|---------|---------------|
| Password hashing | bcrypt, cost factor 12 |
| Token storage | httpOnly, Secure, SameSite=Lax cookies |
| CSRF protection | SameSite cookie + Origin header validation |
| Rate limiting | Existing SlowAPI middleware (extend to auth endpoints) |
| SQL injection | SQLAlchemy ORM parameterized queries (existing) |
| XSS | Vue.js auto-escaping + Content-Security-Policy headers |
| Input validation | Pydantic schemas on all endpoints (existing pattern) |
| CORS | Existing whitelist (extend for new frontend routes) |

### 12.4 Authorization Rules

| Action | Who Can Do It |
|--------|--------------|
| View free tips | Anyone (no auth) |
| Register/login | Anyone |
| Join competition | Any authenticated user |
| Create competition | Any authenticated user (after payment) |
| Edit comp settings | Competition host only |
| Submit tips | Competition members only (before deadline) |
| View leaderboard | Competition members only |
| View settlement | Competition members only |
| Generate settlement | Competition host only (Prize Split tier) |
| Post chat message | Competition members only |
| Delete chat message | Message author or competition host |

---

## 13. Stripe Integration

### 13.1 Payment Flow

```
1. User clicks "Create Competition" on frontend
2. Frontend sends POST /api/payments/checkout with:
   - tier: 'comp_host' or 'comp_host_prize'
   - competition_id: (created in draft state)
3. Backend creates Stripe Checkout Session:
   - mode: 'payment' (one-time, not subscription)
   - line_items: [{ price_data: { currency: 'aud', unit_amount: 500 or 1000 } }]
   - success_url: frontend/payment/success?session_id={CHECKOUT_SESSION_ID}
   - cancel_url: frontend/payment/cancel
   - metadata: { user_id, competition_id, tier }
4. Backend returns session_url to frontend
5. Frontend redirects user to Stripe Checkout
6. User completes payment
7. Stripe redirects to success_url
8. Stripe sends webhook to POST /api/payments/webhook
9. Backend verifies webhook signature
10. Backend creates Purchase record (status='completed')
11. Backend activates competition (is_active=True)
12. Frontend success page shows competition link
```

### 13.2 Stripe Configuration

```python
# In config.py
stripe_secret_key: str = ""         # sk_test_... or sk_live_...
stripe_publishable_key: str = ""    # pk_test_... or pk_live_...
stripe_webhook_secret: str = ""     # whsec_...
```

### 13.3 Price Configuration

```python
PRICES = {
    "comp_host": 500,          # $5.00 AUD in cents
    "comp_host_prize": 1000,   # $10.00 AUD in cents
}
```

### 13.4 Webhook Handling

```python
# Handle checkout.session.completed
# Handle checkout.session.expired (mark purchase as expired)
# Verify signature using stripe_webhook_secret
# Idempotent: check if purchase already completed (use stripe_session_id)
```

### 13.5 Refund Policy

- Full refund available if requested before competition's first round begins
- No refund after first round starts
- Refunds processed manually via Stripe Dashboard initially
- Automated refund endpoint in v2

---

## 14. Frontend Components

### 14.1 New Pages Summary

| Page | Route | Auth Required | Description |
|------|-------|--------------|-------------|
| Register | `/register` | No | Create account |
| Login | `/login` | No | Login form |
| Dashboard | `/dashboard` | Yes | All user's competitions |
| Competitions List | `/competitions` | Yes | Browse + join comps |
| Create Competition | `/competitions/create` | Yes | Wizard with payment |
| Competition Detail | `/competitions/[id]` | Yes (member) | Leaderboard + chat |
| Submit Tips | `/competitions/[id]/tips` | Yes (member) | Tip sheet for round |
| Comp Settings | `/competitions/[id]/settings` | Yes (host) | Edit comp rules |
| Settlement | `/competitions/[id]/settlement` | Yes (host, prize tier) | Prize pool + settlement |
| Join | `/join/[code]` | Yes | Join via invite link |
| Payment Success | `/payment/success` | No | Post-payment confirmation |
| Payment Cancel | `/payment/cancel` | No | Payment cancelled |

### 14.2 Key UI Components

#### Leaderboard Component
- Table layout: Rank, Avatar, Name, Points, Correct Tips, Movement
- Current user row highlighted
- Sticky header for long lists
- Animated rank changes on update
- Mobile: card layout instead of table

#### Tip Sheet Component
- List of games for the round
- Each game shows: Team A vs Team B, venue, time
- Radio buttons for team selection
- Margin input (if margin scoring)
- "Use AI Tips" button with loading state
- Progress indicator: "3/9 tips submitted"
- Lock indicator when deadline passes

#### Settlement Report Component
- Summary card: Total Pool, Number of Transactions
- Transaction list: "Alice pays Bob $20"
- Visual graph showing money flows (SVG arrows between avatars)
- "Copy All" button for settlement text
- "Download PDF" button
- Payment template cards (PayID, bank details)

#### Chat Feed Component
- Reverse-infinite-scroll (load older messages up)
- Message bubble: avatar, name, text, timestamp
- Own messages right-aligned, others left-aligned
- Delete button (own messages) / host sees delete on all
- Character counter (280 max)
- Simple text input at bottom

### 14.3 Design System Additions

- **Sport colours**: Each sport gets a primary colour for branding
  - AFL: Red (#E8292D)
  - NRL: Blue (#0054A6)
  - BBL: Teal (#00B4A0)
  - A-League: Green (#00843D)
  - Super Rugby: Gold (#D4A843)
- **Tier badges**: Small icons/badges for Comp Host and Prize Split tiers
- **Payment flow**: Clean, minimal Stripe Checkout — no custom payment UI needed

---

## 15. Testing Strategy

### 15.1 Backend Tests

#### Unit Tests

| Test Suite | Coverage Target | Key Tests |
|------------|----------------|-----------|
| `test_auth_service.py` | 95% | Password hashing, JWT generation/validation, token expiry |
| `test_settlement_service.py` | 100% | Algorithm correctness, edge cases (ties, zero balances, single debtor) |
| `test_competition_service.py` | 90% | Scoring logic, deadline enforcement, invite code generation |
| `test_payment_service.py` | 90% | Session creation, webhook handling, idempotency |
| `test_scoring_service.py` | 95% | Standard scoring, margin scoring, late tip handling |

#### Integration Tests

| Test Suite | Coverage Target | Key Tests |
|------------|----------------|-----------|
| `test_auth_api.py` | 90% | Full registration/login flow, rate limiting, email verification |
| `test_competitions_api.py` | 90% | CRUD, join/leave, authorization, payment gating |
| `test_user_tips_api.py` | 90% | Tip submission, deadline locking, auto-ai fill |
| `test_settlements_api.py` | 90% | Prize pool config, settlement generation, PDF download |
| `test_payments_api.py` | 85% | Checkout flow, webhook processing, purchase verification |

#### Settlement Algorithm Test Cases

```python
# Must cover:
1. Simple case: 4 members, winner-takes-all
2. Top 3 split: 60/30/10
3. Tied ranks: two members with same score
4. Wooden spoon bonus
5. Everyone ties (all get money back)
6. Single member (edge case)
7. Large comp: 50 members
8. Zero entry fee (free comp, no settlement)
9. Rounding: ensure no cents lost in minimization
10. Partial amounts: uneven splits
```

### 15.2 Frontend Tests

| Test Type | Tool | Coverage |
|-----------|------|----------|
| Component tests | Vitest + Vue Test Utils | Core components |
| E2E flows | Playwright | Critical user paths |
| Visual regression | Playwright screenshots | Key pages |

#### Critical E2E Flows

1. **Registration to first tip**: Register -> verify email -> join comp -> submit tips
2. **Create and pay for comp**: Login -> create comp -> Stripe checkout -> success
3. **Full season flow**: Create comp -> invite members -> submit tips -> view leaderboard -> settlement
4. **Multi-sport navigation**: Switch between AFL and NRL -> view tips for each

### 15.3 Test Data Strategy

- Use factory fixtures for User, Competition, Game, Tip models
- Mock Stripe API in tests (use stripe-mock or responses library)
- Mock external sport APIs for multi-sport tests
- Seed test database with 1 full season of AFL data (existing)
- Generate synthetic data for other sports

---

## 16. Migration Plan

### 16.1 Phase Overview

The rollout is structured in 4 phases to minimize risk and allow incremental testing.

#### Phase 1: Foundation (Weeks 1–2)
**Goal**: User authentication + database schema

- [ ] Add `sport` column to `games` table (migration)
- [ ] Create `users` table (migration)
- [ ] Implement `auth_service.py` (register, login, JWT)
- [ ] Implement `auth.py` API router
- [ ] Frontend: register/login pages, auth composable
- [ ] Tests: auth unit + integration tests
- [ ] **Checkpoint**: Users can register, login, and see their profile

#### Phase 2: Competitions (Weeks 3–5)
**Goal**: Competition CRUD + tip submission + scoring

- [ ] Create `competitions`, `competition_members`, `user_tips`, `competition_messages` tables
- [ ] Implement `competition_service.py` (create, join, scoring)
- [ ] Implement `competitions.py` API router
- [ ] Implement `user_tips.py` CRUD and API
- [ ] Implement `chat.py` API
- [ ] Extend `match_completion` cron to trigger scoring
- [ ] Frontend: competition pages, tip sheet, leaderboard, chat
- [ ] Tests: competition unit + integration tests
- [ ] **Checkpoint**: Users can create comps (free), join, submit tips, see leaderboard

#### Phase 3: Payments (Weeks 6–7)
**Goal**: Stripe integration + payment-gated competition creation

- [ ] Create `purchases` table
- [ ] Implement `payment_service.py` (Stripe Checkout)
- [ ] Implement `payments.py` API router + webhook handler
- [ ] Gate competition creation behind payment
- [ ] Frontend: payment flow, success/cancel pages
- [ ] Tests: payment unit + integration tests (mocked Stripe)
- [ ] **Checkpoint**: Users can pay $5/$10 to create competitions

#### Phase 4: Settlement + Multi-Sport (Weeks 8–10)
**Goal**: Prize pool calculator + second sport (NRL)

- [ ] Create `prize_pools` and `settlements` tables
- [ ] Implement `settlement_service.py` (transaction minimization)
- [ ] Implement `settlements.py` API router
- [ ] Implement PDF report generation
- [ ] Implement NRL data provider (SportDataProvider)
- [ ] Add NRL sync cron job
- [ ] Frontend: settlement pages, sport selector
- [ ] Tests: settlement algorithm (100% coverage), NRL integration
- [ ] **Checkpoint**: Full feature set working for AFL + NRL

### 16.2 Migration Files Required

```
alembic/versions/
  2026_05_20_001_add_users_table.py
  2026_05_20_002_add_sport_to_games.py
  2026_05_27_001_add_competitions_tables.py
  2026_05_27_002_add_user_tips_table.py
  2026_05_27_003_add_competition_messages_table.py
  2026_06_03_001_add_purchases_table.py
  2026_06_10_001_add_prize_pools_table.py
  2026_06_10_002_add_settlements_table.py
```

### 16.3 Rollback Strategy

- Each migration must have a working `downgrade()` function
- Test all downgrades in CI before merging
- Database backups before each phase deployment
- Feature flags for each phase (enable/disable without redeployment)

---

## 17. Legal Considerations

### 17.1 Why This Is Not Gambling

The platform is a **SaaS tool** (like Splitwise or SettleUp), not a gambling operator:

| Factor | Gambling | WhatIsMyTip |
|--------|----------|-------------|
| Takes bets | Yes | No |
| Holds prize money | Yes | No |
| Determines odds | Yes | No (tips are predictions, not odds) |
| Requires gambling licence | Yes | No |
| Profit from stakes | Yes | No (profit from SaaS subscription) |

The platform:
- **Never touches money** between users
- **Does not facilitate betting** — tips are for entertainment
- **Charges for software**, not for gambling services
- **Settlement calculator** is a math tool, not a financial service

### 17.2 Required Legal Documents

| Document | Purpose | Priority |
|----------|---------|----------|
| Terms of Service | Usage terms, liability limitations | **Must have** (before launch) |
| Privacy Policy | Data handling, GDPR/APP compliance | **Must have** (before launch) |
| Responsible Gambling Disclaimer | On any betting affiliate content | **Must have** (if affiliates added) |
| Settlement Disclaimer | "Platform does not handle money" | **Must have** (before settlement launch) |
| Cookie Policy | Cookie usage disclosure | Should have |
| Acceptable Use Policy | Comp chat behaviour guidelines | Should have |

### 17.3 Australian Privacy Principles (APPs)

As an Australian service collecting personal data:

1. **APP 1**: Open and transparent management of personal information
2. **APP 3**: Collect only necessary information (email, display name)
3. **APP 6**: Use data only for the purpose collected
4. **APP 8**: Cross-border disclosure (hosting may be overseas)
5. **APP 11**: Security of personal information (encryption, access controls)
6. **APP 12**: Access to personal information (user can view/delete their data)

### 17.4 Betting Affiliate Compliance

If betting affiliate links are added in future:

- Must display responsible gambling message: "Gamble responsibly. Know your odds."
- Must link to Gambling Help Online (1800 858 858)
- Must not target minors or vulnerable persons
- Must comply with each bookmaker's affiliate terms
- Must not represent tips as guaranteed outcomes

---

## 18. Go-To-Market Strategy

### 18.1 Pre-Launch (Weeks 1–4)

1. **Landing page**: Add "Coming Soon" section for competitions on existing site
2. **Email list**: Collect interest via email signup on landing page
3. **Social media**: Tease competition feature on Twitter/X, Reddit (r/AFL)
4. **Beta testers**: Recruit 20–30 users from existing traffic for closed beta

### 18.2 Launch (Phase 3 Complete)

1. **ProductHunt launch**: "AI-powered tipping competitions for Aussie sports"
2. **Reddit AMA**: r/AFL, r/NRL — demonstrate the product
3. **Twitter/X thread**: "I built a tipping comp platform that uses AI"
4. **Word of mouth**: Each comp host brings 5–15 users organically

### 18.3 Growth Levers

1. **Viral loop**: Free to join comps = low barrier = more users = more potential hosts
2. **SEO**: Existing free tips drive traffic; competition pages add more content
3. **Social proof**: "Join 500 tippers this season" on homepage
4. **Multi-sport**: Each new sport is a new launch event (NRL launch, BBL launch, etc.)
5. **Referral incentive**: "Invite 3 friends to create comps, get your next comp free"

### 18.4 Retention Mechanics

1. **Round reminders**: Email/push before each tipping deadline
2. **Leaderboard notifications**: "You've moved up to 3rd place!"
3. **Season recap**: End-of-season summary with stats, highlights, shareable graphic
4. **Early bird pricing**: Renew for next season at a discount
5. **Cross-sport prompts**: "NRL starts next week — create a comp!"

---

## 19. Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | Legal challenge re gambling classification | Low | Critical | Legal review before launch; platform never touches money; clear disclaimers |
| 2 | Stripe account rejection | Low | High | Apply early; have PayPal as backup; clear business model documentation |
| 3 | Low conversion rate (<2%) | Medium | High | A/B test pricing; add more value to paid tiers; viral mechanics |
| 4 | Multi-sport data source unreliability | Medium | Medium | Cache data locally; fallback to manual entry; multiple API sources |
| 5 | Performance degradation with user growth | Low | Medium | Database indexing; query optimization; caching layer (Redis) |
| 6 | User data breach | Low | Critical | bcrypt hashing; encrypted sensitive fields; security headers; regular audits |
| 7 | Competition scoring errors | Medium | High | 100% test coverage on scoring logic; manual audit for first season |
| 8 | Stripe webhook failures | Medium | Medium | Idempotent handlers; retry logic; manual reconciliation dashboard |
| 9 | Chat abuse/harassment | Medium | Low | Host moderation; rate limiting; report functionality; acceptable use policy |
| 10 | Season timing misalignment | Low | Medium | Configurable season dates; manual override for round detection |

---

## 20. Appendices

### Appendix A: Glossary

| Term | Definition |
|------|-----------|
| Comp | Short for competition — a tipping competition |
| Host | The user who created and paid for a competition |
| Member | A user who joined a competition (free) |
| Tip | A prediction of which team will win a game |
| Round | A set of games played in a single week/round |
| Season | A full year of a sport (e.g., AFL 2026 season) |
| Settlement | The process of calculating who owes whom after a comp ends |
| Net position | How much a member is owed (positive) or owes (negative) |
| Transaction minimization | Algorithm to reduce the number of payments needed |
| Heuristic | A strategy for selecting tips from ML model predictions |
| First bounce | The start time of the first game in a round |

### Appendix B: Environment Variables

```env
# Existing
DATABASE_URL=sqlite+aiosqlite:///./whatismytip.db
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free
CORS_ORIGINS=http://localhost:3000

# New (Phase 1 - Auth)
AUTH_SECRET_KEY=your-jwt-secret-key-here
AUTH_TOKEN_EXPIRY_HOURS=168

# New (Phase 3 - Payments)
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...

# New (Phase 4 - Multi-Sport)
SPORTRADAR_API_KEY=...
CRICKET_DATA_API_KEY=...
FOOTBALL_DATA_API_KEY=...

# New (Email - Phase 1)
SMTP_HOST=smtp.mailgun.org
SMTP_PORT=587
SMTP_USER=...
SMTP_PASS=...
EMAIL_FROM=noreply@whatismytip.com
```

### Appendix C: Dependencies to Add

#### Backend (pyproject.toml)

```toml
[project]
dependencies = [
    # Existing...
    "bcrypt>=4.0.0",           # Password hashing
    "PyJWT>=2.8.0",            # JWT token generation
    "stripe>=9.0.0",           # Stripe payment processing
    "reportlab>=4.0.0",        # PDF generation for settlement reports
    "aiosmtplib>=3.0.0",       # Async email sending
]

[project.optional-dependencies]
dev = [
    # Existing...
    "stripe-mock>=0.1.0",      # Mock Stripe API for tests
    "factory-boy>=3.3.0",      # Test data factories
]
```

#### Frontend (package.json)

```json
{
  "dependencies": {
    "@stripe/stripe-js": "^4.0.0"
  }
}
```

### Appendix D: Database ERD Summary

```
users 1---N competitions (host)
users 1---N competition_members
users 1---N user_tips
users 1---N purchases
users 1---N competition_messages

competitions 1---N competition_members
competitions 1---N user_tips
competitions 1---1 prize_pools
competitions 1---N settlements
competitions 1---N competition_messages
competitions 1---N purchases

games 1---N user_tips
games 1---N tips (existing AI tips)

prize_pools 1---N settlements
```

### Appendix E: Implementation Priority Matrix

| Feature | Business Value | Technical Complexity | Priority |
|---------|---------------|---------------------|----------|
| User auth | High | Medium | P0 (Phase 1) |
| Competition CRUD | High | Medium | P0 (Phase 2) |
| Tip submission | High | Low | P0 (Phase 2) |
| Leaderboard | High | Low | P0 (Phase 2) |
| Stripe payments | High | Medium | P0 (Phase 3) |
| Prize pool config | Medium | Low | P1 (Phase 4) |
| Settlement algorithm | Medium | Medium | P1 (Phase 4) |
| Chat | Low | Low | P2 (Phase 2) |
| PDF reports | Low | Low | P2 (Phase 4) |
| Multi-sport (NRL) | Medium | High | P1 (Phase 4) |
| Multi-sport (BBL) | Medium | High | P2 (Post-launch) |
| Multi-sport (A-League) | Low | High | P3 (Post-launch) |
| Multi-sport (Super Rugby) | Low | High | P3 (Post-launch) |
| Betting affiliates | Medium | Low | P3 (Post-launch) |
| B2B venue licensing | Low | High | P3 (Post-launch) |

---

*End of specification. This document should be used as the source of truth for spec-driven development. Each phase should be broken down into individual feature specs with detailed acceptance tests before implementation begins.*
