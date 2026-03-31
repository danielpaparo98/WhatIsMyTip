<template>
  <div class="game-card">
    <div class="game-header">
      <span class="round">R{{ roundId }}</span>
      <span class="date">{{ formatDate(date) }}</span>
    </div>
    <div class="game-body">
      <div class="team home">
        <img :src="getLogoUrl(homeTeam)" :alt="homeTeam" class="team-logo" />
        <span class="team-name">{{ homeTeam }}</span>
        <span v-if="homeScore !== null" class="score">{{ homeScore }}</span>
      </div>
      <div class="vs">VS</div>
      <div class="team away">
        <img :src="getLogoUrl(awayTeam)" :alt="awayTeam" class="team-logo" />
        <span class="team-name">{{ awayTeam }}</span>
        <span v-if="awayScore !== null" class="score">{{ awayScore }}</span>
      </div>
    </div>
    <p class="venue">{{ venue }}</p>
  </div>
</template>

<script setup lang="ts">
interface Props {
  roundId: number
  date: string
  homeTeam: string
  awayTeam: string
  venue: string
  homeScore?: number | null
  awayScore?: number | null
}

defineProps<Props>()

const formatDate = (dateStr: string) => {
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-AU', {
    weekday: 'short',
    day: 'numeric',
    month: 'short'
  })
}

const getLogoUrl = (teamName: string): string => {
  // Map team names to logo filenames
  const logoMap: Record<string, string> = {
    'Adelaide': 'Adelaide.png',
    'Brisbane Lions': 'Brisbane.png',
    'Carlton': 'Carlton.png',
    'Collingwood': 'Collingwood.png',
    'Essendon': 'Essendon.png',
    'Fremantle': 'Fremantle.png',
    'Geelong': 'Geelong.png',
    'Gold Coast': 'GoldCoast.png',
    'Greater Western Sydney': 'Giants.png',
    'Hawthorn': 'Hawthorn.png',
    'Melbourne': 'Melbourne.png',
    'North Melbourne': 'NorthMelbourne.png',
    'Port Adelaide': 'PortAdelaide.png',
    'Richmond': 'Richmond.png',
    'St Kilda': 'StKilda.png',
    'Sydney': 'Sydney.png',
    'West Coast': 'WestCoast.png',
    'Western Bulldogs': 'Bulldogs.png',
  }
  
  const filename = logoMap[teamName] || ''
  return filename ? `/logos/${filename}` : ''
}
</script>

<style scoped>
.game-card {
  border: 1px solid var(--color-border);
  padding: 1.25rem;
}

.game-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 1.25rem;
  padding-bottom: 0.875rem;
  border-bottom: 1px solid var(--color-border);
}

.round {
  font-weight: 700;
  font-size: 0.8125rem;
}

.date {
  font-size: 0.8125rem;
  color: var(--color-muted);
}

.game-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.875rem;
}

.team {
  flex: 1;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
}

.team-logo {
  width: 56px;
  height: 56px;
  object-fit: contain;
}

.team-name {
  display: block;
  font-weight: 700;
  font-size: 1rem;
}

.score {
  display: block;
  font-size: 1.75rem;
  font-weight: 800;
  margin-top: 0.375rem;
}

.vs {
  font-size: 0.8125rem;
  font-weight: 700;
  padding: 0 0.75rem;
}

.venue {
  text-align: center;
  font-size: 0.8125rem;
  color: var(--color-muted);
  margin: 0;
}

/* Mobile styles */
@media (max-width: 640px) {
  .game-card {
    padding: 1rem;
  }

  .game-header {
    margin-bottom: 1rem;
    padding-bottom: 0.75rem;
  }

  .round {
    font-size: 0.75rem;
  }

  .date {
    font-size: 0.75rem;
  }

  .game-body {
    margin-bottom: 0.75rem;
  }

  .team-logo {
    width: 48px;
    height: 48px;
  }

  .team-name {
    font-size: 0.9375rem;
  }

  .score {
    font-size: 1.5rem;
  }

  .vs {
    font-size: 0.75rem;
    padding: 0 0.5rem;
  }

  .venue {
    font-size: 0.75rem;
  }
}

/* Tablet styles */
@media (min-width: 641px) and (max-width: 1024px) {
  .team-logo {
    width: 60px;
    height: 60px;
  }

  .team-name {
    font-size: 1.0625rem;
  }

  .score {
    font-size: 1.875rem;
  }
}

/* Desktop styles */
@media (min-width: 1025px) {
  .game-card {
    padding: 1.5rem;
  }

  .game-header {
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
  }

  .round {
    font-size: 0.875rem;
  }

  .date {
    font-size: 0.875rem;
  }

  .game-body {
    margin-bottom: 1rem;
  }

  .team-logo {
    width: 64px;
    height: 64px;
  }

  .team-name {
    font-size: 1.125rem;
  }

  .score {
    font-size: 2rem;
    margin-top: 0.5rem;
  }

  .vs {
    font-size: 0.875rem;
    padding: 0 1rem;
  }

  .venue {
    font-size: 0.875rem;
  }
}
</style>
