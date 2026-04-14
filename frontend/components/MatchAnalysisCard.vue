<template>
  <div class="match-analysis-card">
    <div class="card-header">
      <span class="header-icon">🗣️</span>
      <div class="header-text">
        <h3 class="header-title">BBQ Talking Points</h3>
        <p class="header-subtitle">Casual chat to blend in at the footy</p>
      </div>
    </div>
    <div class="talking-points">
      <p
        v-for="(point, index) in talkingPoints"
        :key="index"
        class="talking-point"
      >
        {{ point }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { MatchAnalysis } from '~/composables/useApi'

interface Props {
  analysis: MatchAnalysis
}

const props = defineProps<Props>()

const talkingPoints = computed(() => {
  return props.analysis.analysis_text
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0)
})
</script>

<style scoped>
.match-analysis-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left: 4px solid #f59e0b;
  padding: 1.5rem;
  transition: border-color 0.2s ease;
}

.match-analysis-card:hover {
  border-color: #f59e0b;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1.25rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.header-icon {
  font-size: 1.5rem;
  flex-shrink: 0;
}

.header-text {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.header-title {
  font-size: 1.125rem;
  font-weight: 700;
  color: #f59e0b;
  margin: 0;
}

.header-subtitle {
  font-size: 0.75rem;
  color: var(--color-muted);
  margin: 0;
  font-style: italic;
}

.talking-points {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.talking-point {
  font-size: 0.9375rem;
  line-height: 1.6;
  color: var(--color-text);
  margin: 0;
  padding-left: 1rem;
  border-left: 2px solid rgba(245, 158, 11, 0.3);
}

/* Mobile */
@media (max-width: 640px) {
  .match-analysis-card {
    padding: 1rem;
  }

  .header-title {
    font-size: 1rem;
  }

  .talking-point {
    font-size: 0.875rem;
    padding-left: 0.75rem;
  }
}
</style>
