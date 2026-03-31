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
  padding: 1.5rem;
}

.game-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.round {
  font-weight: 700;
  font-size: 0.875rem;
}

.date {
  font-size: 0.875rem;
  color: var(--color-muted);
}

.game-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
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
  width: 64px;
  height: 64px;
  object-fit: contain;
}

.team-name {
  display: block;
  font-weight: 700;
  font-size: 1.125rem;
}

.score {
  display: block;
  font-size: 2rem;
  font-weight: 800;
  margin-top: 0.5rem;
}

.vs {
  font-size: 0.875rem;
  font-weight: 700;
  padding: 0 1rem;
}

.venue {
  text-align: center;
  font-size: 0.875rem;
  color: var(--color-muted);
  margin: 0;
}
</style>
