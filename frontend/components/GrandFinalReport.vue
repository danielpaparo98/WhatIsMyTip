<template>
  <article class="gf-report">
    <!-- 1. Hero header -->
    <header class="gf-hero">
      <span class="eyebrow">Grand Final</span>
      <h1 class="headline">{{ report.headline }}</h1>

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

      <div class="hero-meta">
        <span class="meta-item">{{ game.venue ?? 'TBD' }}</span>
        <span class="meta-dot" aria-hidden="true">&bull;</span>
        <span class="meta-item">{{ game.date ? formatDate(game.date) : 'TBD' }}</span>
      </div>

      <p v-if="report.executive_summary" class="standfirst">{{ report.executive_summary }}</p>
    </header>

    <!-- 2. The Verdict -->
    <section v-if="report.prediction" class="gf-section">
      <h2 class="section-label">The Verdict</h2>
      <div class="verdict">
        <span class="verdict-winner">{{ getTeamDisplayName(report.prediction.winner) }}</span>
        <span class="verdict-stats">
          Margin {{ report.prediction.margin }} pts &middot; Confidence
          {{ Math.round(report.prediction.confidence * 100) }}%
        </span>
      </div>
      <div v-if="report.model_consensus" class="consensus">
        <p v-if="report.model_consensus.summary" class="consensus-summary">
          {{ report.model_consensus.summary }}
        </p>
        <p v-if="report.model_consensus.season_accuracy_note" class="consensus-note">
          {{ report.model_consensus.season_accuracy_note }}
        </p>
      </div>
    </section>

    <!-- 3. Season Story -->
    <section v-if="seasonStorySides.length > 0" class="gf-section">
      <h2 class="section-label">Season Story</h2>
      <div class="two-col">
        <div v-for="side in seasonStorySides" :key="side.side" class="story">
          <h3 class="story-team">{{ getTeamDisplayName(side.story.team) }}</h3>
          <p class="story-narrative">{{ side.story.narrative }}</p>
          <ul v-if="side.story.finals_path.length > 0" class="finals-path">
            <li v-for="(step, i) in side.story.finals_path" :key="i">{{ step }}</li>
          </ul>
        </div>
      </div>
    </section>

    <!-- 4. Keys to the Game -->
    <section v-if="keys.length > 0" class="gf-section">
      <h2 class="section-label">Keys to the Game</h2>
      <ul class="key-list">
        <li v-for="(key, i) in keys" :key="i" class="key-item">{{ key }}</li>
      </ul>
    </section>

    <!-- 5. Players to Watch -->
    <section v-if="homePlayers.length > 0 || awayPlayers.length > 0" class="gf-section">
      <h2 class="section-label">Players to Watch</h2>
      <div class="two-col">
        <div v-if="homePlayers.length > 0" class="watch-col">
          <h3 class="col-team">{{ getTeamDisplayName(game.home_team) }}</h3>
          <ul class="watch-list">
            <li v-for="(player, i) in homePlayers" :key="i" class="watch-item">
              <span class="player-name">{{ player.name }}</span>
              <span class="player-note">{{ player.note }}</span>
            </li>
          </ul>
        </div>
        <div v-if="awayPlayers.length > 0" class="watch-col">
          <h3 class="col-team">{{ getTeamDisplayName(game.away_team) }}</h3>
          <ul class="watch-list">
            <li v-for="(player, i) in awayPlayers" :key="i" class="watch-item">
              <span class="player-name">{{ player.name }}</span>
              <span class="player-note">{{ player.note }}</span>
            </li>
          </ul>
        </div>
      </div>
    </section>

    <!-- 6. Injury Watch -->
    <section v-if="homeInjuries.length > 0 || awayInjuries.length > 0" class="gf-section">
      <h2 class="section-label">Injury Watch</h2>
      <div class="two-col">
        <div v-if="homeInjuries.length > 0" class="watch-col">
          <h3 class="col-team">{{ getTeamDisplayName(game.home_team) }}</h3>
          <ul class="watch-list">
            <li v-for="(note, i) in homeInjuries" :key="i" class="watch-item">
              <span class="player-name">{{ note.player }}</span>
              <span class="status-label">{{ note.status }}</span>
              <span class="player-note">{{ note.note }}</span>
            </li>
          </ul>
        </div>
        <div v-if="awayInjuries.length > 0" class="watch-col">
          <h3 class="col-team">{{ getTeamDisplayName(game.away_team) }}</h3>
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

    <!-- 7. Weather Impact + X-Factor -->
    <section v-if="report.weather_impact || report.x_factor" class="gf-section">
      <div class="two-col">
        <div v-if="report.weather_impact" class="fact-card">
          <h3 class="fact-label">Weather Impact</h3>
          <p class="fact-body">{{ report.weather_impact }}</p>
        </div>
        <div v-if="report.x_factor" class="fact-card">
          <h3 class="fact-label">X-Factor</h3>
          <p class="fact-body">{{ report.x_factor }}</p>
        </div>
      </div>
    </section>

    <!-- 8. Talking Points -->
    <section v-if="talkingPoints.length > 0" class="gf-section">
      <h2 class="section-label">Talking Points</h2>
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
} from '~/composables/useApi'
import { formatExplanationImpl } from '~/composables/useFormatters'

interface Props {
  report: GrandFinalReport
  game: Game
}

const props = defineProps<Props>()

const { getLogoUrl, getTeamDisplayName } = useTeamLogos()
const { formatDate } = useFormatters()

// Optional-safe section data: the backend validates the full payload,
// but every list degrades to an empty array so a thin report hides its
// sections instead of crashing the page.
const keys = computed<string[]>(() => props.report.keys_to_the_game ?? [])

const seasonStorySides = computed<Array<{ side: string; story: TeamStory }>>(() => {
  const story = props.report.season_story
  const sides: Array<{ side: string; story: TeamStory }> = []
  if (story?.home) sides.push({ side: 'home', story: story.home })
  if (story?.away) sides.push({ side: 'away', story: story.away })
  return sides
})

const homePlayers = computed<PlayerSpotlight[]>(() => props.report.key_players?.home ?? [])
const awayPlayers = computed<PlayerSpotlight[]>(() => props.report.key_players?.away ?? [])
const homeInjuries = computed<InjuryNote[]>(() => props.report.injury_watch?.home ?? [])
const awayInjuries = computed<InjuryNote[]>(() => props.report.injury_watch?.away ?? [])

const talkingPoints = computed<string[]>(() =>
  (props.report.talking_points ?? [])
    .map(line => formatExplanationImpl(line))
    .filter(line => line.length > 0),
)
</script>

<style scoped>
.gf-report {
  padding: 2.25rem 1.5rem 3rem;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

/* Hero header */
.gf-hero {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left: 4px solid var(--color-text);
  padding: 2rem;
  text-align: center;
}

.eyebrow {
  display: inline-block;
  font-size: 0.75rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  border: 1px solid var(--color-text);
  padding: 0.375rem 0.875rem;
  margin-bottom: 1.25rem;
}

.headline {
  font-size: clamp(1.75rem, 5vw, 3.5rem);
  line-height: 1.1;
  margin-bottom: 1.75rem;
}

.teams {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2rem;
  margin-bottom: 1.25rem;
}

.team {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  flex: 1;
}

.team-logo {
  width: 80px;
  height: 80px;
  object-fit: contain;
}

.team-name {
  font-weight: 700;
  font-size: 1.125rem;
  text-align: center;
}

.vs {
  font-size: 1.125rem;
  font-weight: 700;
  color: var(--color-muted);
}

.hero-meta {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  font-size: 0.875rem;
  color: var(--color-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 700;
  margin-bottom: 1.25rem;
}

.standfirst {
  font-size: 1.125rem;
  line-height: 1.7;
  color: var(--color-muted);
  max-width: 720px;
  margin: 0 auto;
}

/* Shared section card — matches the MatchAnalysisCard treatment */
.gf-section {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left: 4px solid var(--color-text);
  padding: 1.5rem;
  transition: border-color 0.2s ease;
}

.gf-section:hover {
  border-color: var(--color-text);
}

.section-label {
  font-size: 1.125rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin: 0 0 1.25rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--color-border);
}

.two-col {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1.5rem;
}

/* The Verdict */
.verdict {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 1.25rem;
}

.verdict-winner {
  font-size: clamp(1.5rem, 4vw, 2.5rem);
  font-weight: 800;
  line-height: 1.15;
}

.verdict-stats {
  font-size: 0.8125rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-muted);
  font-variant-numeric: tabular-nums;
}

.consensus-summary {
  margin: 0 0 0.5rem;
  font-size: 1rem;
  line-height: 1.6;
  padding-left: 1rem;
  border-left: 2px solid var(--color-border);
}

.consensus-note {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-muted);
  font-style: italic;
}

/* Season Story */
.story-team {
  font-size: 1.125rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0 0 0.75rem;
}

.story-narrative {
  margin: 0 0 0.875rem;
  font-size: 0.9375rem;
  line-height: 1.6;
}

.finals-path {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.finals-path li {
  font-size: 0.875rem;
  font-weight: 700;
  padding-left: 1rem;
  border-left: 2px solid var(--color-border);
}

/* Keys to the Game */
.key-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.key-item {
  font-size: 0.9375rem;
  line-height: 1.6;
  padding: 0.875rem 1rem 0.875rem 1.25rem;
  background: var(--color-hover);
  border-left: 4px solid var(--color-text);
}

/* Players / Injuries */
.col-team {
  font-size: 0.8125rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
  margin: 0 0 0.875rem;
}

.watch-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.watch-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding-left: 1rem;
  border-left: 2px solid var(--color-border);
}

.player-name {
  font-weight: 700;
  font-size: 1rem;
}

.status-label {
  align-self: flex-start;
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  border: 1px solid var(--color-border);
  padding: 0.125rem 0.5rem;
}

.player-note {
  font-size: 0.875rem;
  line-height: 1.55;
  color: var(--color-muted);
}

/* Weather / X-Factor cards */
.fact-card {
  border: 1px solid var(--color-border);
  padding: 1.25rem;
}

.fact-label {
  font-size: 0.8125rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin: 0 0 0.75rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--color-border);
}

.fact-body {
  margin: 0;
  font-size: 0.9375rem;
  line-height: 1.6;
}

/* Talking Points */
.talking-points {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.talking-point {
  font-size: 0.9375rem;
  line-height: 1.6;
  margin: 0;
  padding-left: 1rem;
  border-left: 2px solid var(--color-border);
}

/* Mobile */
@media (max-width: 640px) {
  .gf-report {
    padding: 2rem 1rem;
    gap: 1rem;
  }

  .gf-hero {
    padding: 1.5rem 1rem;
  }

  .teams {
    gap: 1rem;
  }

  .team-logo {
    width: 64px;
    height: 64px;
  }

  .team-name {
    font-size: 1rem;
  }

  .vs {
    font-size: 1rem;
  }

  .gf-section {
    padding: 1rem;
  }
}

/* Tablet */
@media (min-width: 641px) and (max-width: 1024px) {
  .team-logo {
    width: 72px;
    height: 72px;
  }

  .two-col {
    grid-template-columns: repeat(2, 1fr);
  }
}

/* Desktop */
@media (min-width: 1025px) {
  .gf-report {
    padding: 4rem 2rem;
    max-width: 1200px;
    margin: 0 auto;
  }

  .gf-hero {
    padding: 2.5rem;
  }

  .team-logo {
    width: 96px;
    height: 96px;
  }

  .team-name {
    font-size: 1.25rem;
  }

  .two-col {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
