<template>
  <div class="tip-card">
    <div class="tip-header">
      <span class="heuristic">{{ heuristicLabel }}</span>
      <span class="confidence">{{ Math.round(confidence * 100) }}%</span>
    </div>
    <div class="tip-body">
      <h3>{{ selectedTeam }}</h3>
      <p class="margin">Margin: {{ margin }} pts</p>
    </div>
    <p v-if="explanation" class="explanation">{{ explanation }}</p>
  </div>
</template>

<script setup lang="ts">
interface Props {
  heuristic: string
  selectedTeam: string
  margin: number
  confidence: number
  explanation?: string
}

const props = defineProps<Props>()

const heuristicLabel = computed(() => {
  const labels: Record<string, string> = {
    best_bet: 'Best Bet',
    yolo: 'YOLO',
    high_risk_high_reward: 'High Risk / High Reward'
  }
  return labels[props.heuristic] || props.heuristic
})
</script>

<style scoped>
.tip-card {
  border: 1px solid var(--color-border);
  padding: 1.5rem;
  transition: border-color 0.2s ease;
}

.tip-card:hover {
  border-color: var(--color-text);
}

.tip-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.heuristic {
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-muted);
}

.confidence {
  font-size: 0.875rem;
  font-weight: 700;
}

.tip-body h3 {
  font-size: 1.5rem;
  margin-bottom: 0.5rem;
}

.margin {
  font-size: 0.875rem;
  margin: 0;
}

.explanation {
  margin-top: 1rem;
  font-size: 0.9375rem;
  line-height: 1.5;
}
</style>
