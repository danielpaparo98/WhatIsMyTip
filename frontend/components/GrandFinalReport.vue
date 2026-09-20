<template>
  <!--
    EDITORIAL REDESIGN (2026-09-20, user direction): the report reads as
    a centered magazine column — hairline section rules and typographic
    hierarchy instead of stacked cards.  One eyebrow total ("Grand
    Final"); every section after it is a numbered small-caps label over
    a hairline.  Monochrome tokens only; the confetti is the colour
    moment.
  -->
  <article class="gf-report">
    <!-- Report header -->
    <header class="gf-head">
      <span class="eyebrow">Grand Final</span>
      <div class="teams">
        <div class="team">
          <img
            :src="getLogoUrl(game.home_team ?? 'TBD')"
            :alt="`${game.home_team ?? 'TBD'} logo`"
            class="team-logo"
            loading="lazy"
            decoding="async"
            width="96"
            height="96"
          />
          <span class="team-name">{{ getTeamDisplayName(game.home_team) }}</span>
        </div>
        <span class="vs">VS</span>
        <div class="team">
          <img
            :src="getLogoUrl(game.away_team ?? 'TBD')"
            :alt="`${game.away_team ?? 'TBD'} logo`"
            class="team-logo"
            loading="lazy"
            decoding="async"
            width="96"
            height="96"
          />
          <span class="team-name">{{ getTeamDisplayName(game.away_team) }}</span>
        </div>
      </div>
      <div class="head-meta">
        <span class="meta-item">{{ game.venue ?? 'TBD' }}</span>
        <span class="meta-dot" aria-hidden="true">&bull;</span>
        <span class="meta-item">{{ game.date ? formatDate(game.date) : 'TBD' }}</span>
      </div>
      <h1 class="headline">{{ report.headline }}</h1>
      <p v-if="report.executive_summary" class="lede">{{ report.executive_summary }}</p>
    </header>

    <!-- 01 · The Verdict -->
    <section v-if="report.prediction" class="gf-section">
      <h2 class="section-label"><span class="section-no">01</span>The Verdict</h2>
      <p class="verdict">
        <span class="verdict-winner">{{ getTeamDisplayName(report.prediction.winner) }}</span>
        <span class="verdict-stats">
          by {{ report.prediction.margin }} pts &middot;
          {{ Math.round(report.prediction.confidence * 100) }}% confidence
        </span>
      </p>
      <p v-if="report.model_consensus?.summary" class="prose">
        {{ report.model_consensus.summary }}
      </p>
      <p v-if="report.model_consensus?.season_accuracy_note" class="prose prose-muted">
        {{ report.model_consensus.season_accuracy_note }}
      </p>
    </section>

    <!-- 02 · The Tips -->
    <section v-if="tips.length > 0" class="gf-section">
      <h2 class="section-label"><span class="section-no">02</span>The Tips</h2>
      <ul class="tip-rows">
        <li v-for="tip in tips" :key="tip.heuristic" class="tip-row">
          <span class="tip-heuristic">{{ formatHeuristic(tip.heuristic) }}</span>
          <span class="tip-team">{{ getTeamDisplayName(tip.selected_team) }}</span>
          <span class="tip-score">
            by {{ tip.margin }} pts &middot; {{ Math.round(tip.confidence * 100) }}%
          </span>
        </li>
      </ul>
    </section>

    <!-- 03 · Season Story -->
    <section v-if="seasonStorySides.length > 0" class="gf-section">
      <h2 class="section-label"><span class="section-no">03</span>Season Story</h2>
      <div class="two-col">
        <div v-for="side in seasonStorySides" :key="side.side" class="story">
          <h3 class="sub-head">{{ getTeamDisplayName(side.story.team) }}</h3>
          <p class="prose">{{ side.story.narrative }}</p>
          <ol v-if="side.story.finals_path.length > 0" class="path-list">
            <li v-for="(step, i) in side.story.finals_path" :key="i">{{ step }}</li>
          </ol>
        </div>
      </div>
    </section>

    <!-- 04 · Keys to the Game -->
    <section v-if="keys.length > 0" class="gf-section">
      <h2 class="section-label"><span class="section-no">04</span>Keys to the Game</h2>
      <ol class="key-list">
        <li v-for="(key, i) in keys" :key="i" class="key-item">
          <span class="key-no">{{ String(i + 1).padStart(2, '0') }}</span>
          <span class="key-text">{{ key }}</span>
        </li>
      </ol>
    </section>

    <!-- 05 · Players to Watch -->
    <section v-if="homePlayers.length > 0 || awayPlayers.length > 0" class="gf-section">
      <h2 class="section-label"><span class="section-no">05</span>Players to Watch</h2>
      <div class="two-col">
        <div v-if="homePlayers.length > 0" class="watch-col">
          <h3 class="sub-head">{{ getTeamDisplayName(game.home_team) }}</h3>
          <ul class="watch-list">
            <li v-for="(player, i) in homePlayers" :key="i" class="watch-item">
              <span class="player-name">{{ player.name }}</span>
              <span class="player-note">{{ player.note }}</span>
            </li>
          </ul>
        </div>
        <div v-if="awayPlayers.length > 0" class="watch-col">
          <h3 class="sub-head">{{ getTeamDisplayName(game.away_team) }}</h3>
          <ul class="watch-list">
            <li v-for="(player, i) in awayPlayers" :key="i" class="watch-item">
              <span class="player-name">{{ player.name }}</span>
              <span class="player-note">{{ player.note }}</span>
            </li>
          </ul>
        </div>
      </div>
    </section>

    <!-- 06 · Injury Watch (hidden entirely when no data) -->
    <section v-if="homeInjuries.length > 0 || awayInjuries.length > 0" class="gf-section">
      <h2 class="section-label"><span class="section-no">06</span>Injury Watch</h2>
      <div class="two-col">
        <div v-if="homeInjuries.length > 0" class="watch-col">
          <h3 class="sub-head">{{ getTeamDisplayName(game.home_team) }}</h3>
          <ul class="watch-list">
            <li v-for="(note, i) in homeInjuries" :key="i" class="watch-item">
              <span class="player-name">{{ note.player }}</span>
              <span class="status-label">{{ note.status }}</span>
              <span class="player-note">{{ note.note }}</span>
            </li>
          </ul>
        </div>
        <div v-if="awayInjuries.length > 0" class="watch-col">
          <h3 class="sub-head">{{ getTeamDisplayName(game.away_team) }}</h3>
          <ul class="watch-list">
            <li v-for="(note, i) in awayInjuries" :key="i" class="watch-item">
              <span class="player-name">{{ note.player }}</span>
              <span class="status-label">{{ note.status }}</span>
              <span class="player-note">{{ note.note }}</span>
            </li>
          </ul>
        </div>
      </div>
    </section>

    <!-- 07 · Conditions & X-Factor (each half hidden when the agent
         could not assess it — no "unavailable" filler prose) -->
    <section v-if="weatherVisible || xFactorVisible" class="gf-section">
      <h2 class="section-label"><span class="section-no">07</span>Conditions &amp; X-Factor</h2>
      <div class="two-col">
        <div v-if="weatherVisible" class="fact">
          <h3 class="sub-head">Weather</h3>
          <p class="prose">{{ report.weather_impact }}</p>
        </div>
        <div v-if="xFactorVisible" class="fact">
          <h3 class="sub-head">X-Factor</h3>
          <p class="prose">{{ report.x_factor }}</p>
        </div>
      </div>
    </section>

    <!-- 08 · Talking Points -->
    <section v-if="talkingPoints.length > 0" class="gf-section">
      <h2 class="section-label"><span class="section-no">08</span>Talking Points</h2>
      <div class="talking-points">
        <p v-for="(point, i) in talkingPoints" :key="i" class="talking-point">
          {{ point }}
        </p>
      </div>
    </section>
  </article>
</template>

<script setup lang="ts">
import type {
  Game,
  GrandFinalReport,
  InjuryNote,
  PlayerSpotlight,
  TeamStory,
  Tip,
} from '~/composables/useApi'
import { formatExplanationImpl, sortByHeuristicOrder } from '~/composables/useFormatters'

interface Props {
  report: GrandFinalReport
  game: Game
  /** Heuristic tips for the game (from the game-detail payload). */
  tips?: Tip[]
}

const props = withDefaults(defineProps<Props>(), { tips: () => [] })

const { getLogoUrl, getTeamDisplayName } = useTeamLogos()
const { formatDate, formatHeuristic } = useFormatters()

// Optional-safe section data: every list degrades to an empty array so
// a thin report hides its sections instead of crashing the page.
const keys = computed<string[]>(() => props.report.keys_to_the_game ?? [])

const seasonStorySides = computed<Array<{ side: string; story: TeamStory }>>(() => {
  const story = props.report.season_story
  const sides: Array<{ side: string; story: TeamStory }> = []
  if (story?.home) sides.push({ side: 'home', story: story.home })
  if (story?.away) sides.push({ side: 'away', story: story.away })
  return sides
})

// GF-CONTENT (2026-09-20, user report): the research agent once emitted
// a placeholder entry ("Unknown" / "leaders unavailable") instead of
// omitting the section — filter those out defensively here as well as
// at the source.
const isPlaceholderPlayer = (p: PlayerSpotlight): boolean =>
  !p.name?.trim() || p.name.trim().toLowerCase() === 'unknown'

const homePlayers = computed<PlayerSpotlight[]>(() =>
  (props.report.key_players?.home ?? []).filter(p => !isPlaceholderPlayer(p)),
)
const awayPlayers = computed<PlayerSpotlight[]>(() =>
  (props.report.key_players?.away ?? []).filter(p => !isPlaceholderPlayer(p)),
)
const homeInjuries = computed<InjuryNote[]>(() => props.report.injury_watch?.home ?? [])
const awayInjuries = computed<InjuryNote[]>(() => props.report.injury_watch?.away ?? [])

// GF-CONTENT: "unavailable" filler prose (legacy stored reports) is
// hidden — an empty section beats a paragraph that says nothing.
const isUnavailableNote = (text: string | null | undefined): boolean =>
  !!text && /unavailable/i.test(text)

const weatherVisible = computed(
  () => !!props.report.weather_impact && !isUnavailableNote(props.report.weather_impact),
)
const xFactorVisible = computed(
  () => !!props.report.x_factor && !isUnavailableNote(props.report.x_factor),
)

const tips = computed<Tip[]>(() => sortByHeuristicOrder(props.tips))

const talkingPoints = computed<string[]>(() =>
  (props.report.talking_points ?? [])
    .map(line => formatExplanationImpl(line))
    .filter(line => line.length > 0),
)
</script>

<style scoped>
/* Editorial column: centered, ~72ch, generous rhythm. */
.gf-report {
  max-width: 760px;
  margin: 0 auto;
  padding: 0 0.25rem 3rem;
  display: flex;
  flex-direction: column;
}

/* Header */
.gf-head {
  text-align: center;
  padding: 1rem 0 2.25rem;
  border-bottom: 1px solid var(--color-border);
}

.eyebrow {
  display: inline-block;
  font-size: 0.6875rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.18em;
  border: 1px solid var(--color-text);
  padding: 0.375rem 0.875rem;
  margin-bottom: 1.5rem;
}

.teams {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2.5rem;
  margin-bottom: 1rem;
}

.team {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.625rem;
  flex: 1;
}

.team-logo {
  width: 88px;
  height: 88px;
  object-fit: contain;
}

.team-name {
  font-weight: 800;
  font-size: 1.0625rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.vs {
  font-size: 0.8125rem;
  font-weight: 800;
  color: var(--color-muted);
}

.head-meta {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  font-size: 0.8125rem;
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 700;
  margin-bottom: 1.75rem;
}

.headline {
  font-size: clamp(1.75rem, 4.5vw, 3rem);
  line-height: 1.12;
  margin-bottom: 1.25rem;
  text-wrap: balance;
}

.lede {
  font-size: 1.0625rem;
  line-height: 1.75;
  color: var(--color-muted);
  max-width: 65ch;
  margin: 0 auto;
}

/* Sections: hairline rule + numbered small-caps label. No cards. */
.gf-section {
  padding: 2rem 0 0.5rem;
  border-top: 1px solid var(--color-border);
  margin-top: 2rem;
}

.section-label {
  display: flex;
  align-items: baseline;
  gap: 0.625rem;
  font-size: 0.8125rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  margin-bottom: 1.25rem;
}

.section-no {
  font-variant-numeric: tabular-nums;
  color: var(--color-muted);
}

.prose {
  font-size: 1rem;
  line-height: 1.75;
  color: var(--color-text);
  margin: 0 0 1rem;
  max-width: 68ch;
}

.prose-muted,
.prose:last-child {
  margin-bottom: 0;
}

.prose-muted {
  color: var(--color-muted);
  font-size: 0.9375rem;
}

/* Verdict */
.verdict {
  display: flex;
  align-items: baseline;
  gap: 0.875rem;
  flex-wrap: wrap;
  margin: 0 0 1.25rem;
}

.verdict-winner {
  font-size: clamp(2rem, 5vw, 3.25rem);
  font-weight: 800;
  line-height: 1;
  letter-spacing: -0.01em;
}

.verdict-stats {
  font-size: 0.9375rem;
  font-weight: 700;
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-variant-numeric: tabular-nums;
}

/* Tips: simple rows with hairline dividers */
.tip-rows {
  list-style: none;
  margin: 0;
  padding: 0;
}

.tip-row {
  display: grid;
  grid-template-columns: minmax(110px, auto) 1fr auto;
  align-items: baseline;
  gap: 1rem;
  padding: 0.875rem 0;
  border-bottom: 1px solid var(--color-border);
}

.tip-row:last-child {
  border-bottom: none;
}

.tip-heuristic {
  font-size: 0.6875rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--color-muted);
}

.tip-team {
  font-weight: 800;
  font-size: 1.0625rem;
}

.tip-score {
  font-size: 0.875rem;
  font-weight: 700;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* Two-column blocks (season story / players / conditions) */
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2rem;
}

.sub-head {
  font-size: 0.8125rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 0.75rem;
}

/* Finals path */
.path-list {
  list-style: none;
  margin: 0.75rem 0 0;
  padding: 0;
}

.path-list li {
  font-size: 0.875rem;
  color: var(--color-muted);
  padding: 0.375rem 0;
  border-bottom: 1px dashed var(--color-border);
}

.path-list li:last-child {
  border-bottom: none;
}

/* Keys: numbered editorial list */
.key-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.key-item {
  display: flex;
  gap: 1rem;
  align-items: baseline;
  padding: 0.875rem 0;
  border-bottom: 1px solid var(--color-border);
}

.key-item:last-child {
  border-bottom: none;
}

.key-no {
  font-size: 0.8125rem;
  font-weight: 800;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}

.key-text {
  font-size: 1rem;
  line-height: 1.65;
}

/* Players / injuries */
.watch-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.watch-item {
  padding: 0.625rem 0;
  border-bottom: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.watch-item:last-child {
  border-bottom: none;
}

.player-name {
  font-weight: 800;
  font-size: 0.9375rem;
}

.status-label {
  align-self: flex-start;
  font-size: 0.625rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--color-muted);
  border: 1px solid var(--color-border);
  padding: 0.125rem 0.5rem;
}

.player-note {
  font-size: 0.875rem;
  line-height: 1.6;
  color: var(--color-muted);
}

/* Talking points */
.talking-points {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.talking-point {
  font-size: 1.0625rem;
  line-height: 1.7;
  font-weight: 600;
  margin: 0;
  padding-left: 1.25rem;
  border-left: 3px solid var(--color-text);
  max-width: 68ch;
}

/* Mobile collapse (explicit, per section) */
@media (max-width: 640px) {
  .gf-report {
    padding-bottom: 2rem;
  }

  .gf-head {
    padding-bottom: 1.75rem;
  }

  .teams {
    gap: 1.25rem;
  }

  .team-logo {
    width: 64px;
    height: 64px;
  }

  .headline {
    font-size: 1.625rem;
  }

  .two-col {
    grid-template-columns: 1fr;
    gap: 1.5rem;
  }

  .tip-row {
    grid-template-columns: 1fr auto;
  }

  .tip-heuristic {
    grid-column: 1 / -1;
  }

  .verdict-winner {
    font-size: 2rem;
  }
}
</style>
