<template>
  <div class="weather-card">
    <div class="card-header">
      <span class="header-icon">🌤️</span>
      <div class="header-text">
        <h3 class="header-title">Match Day Weather</h3>
        <span class="data-badge" :class="weather.data_type">
          {{ weather.data_type === 'historical' ? 'Historical' : 'Forecast' }}
        </span>
      </div>
    </div>
    <div class="weather-grid">
      <div class="weather-item">
        <span class="weather-icon">🌡️</span>
        <div class="weather-detail">
          <span class="weather-label">Temperature</span>
          <span class="weather-value">{{ weather.temperature }}°C</span>
        </div>
      </div>
      <div class="weather-item">
        <span class="weather-icon">💧</span>
        <div class="weather-detail">
          <span class="weather-label">Precipitation</span>
          <span class="weather-value">{{ weather.precipitation }}mm</span>
        </div>
      </div>
      <div class="weather-item">
        <span class="weather-icon">💨</span>
        <div class="weather-detail">
          <span class="weather-label">Wind</span>
          <span class="weather-value">{{ weather.wind_speed }} km/h, gusts {{ weather.wind_gusts }} km/h</span>
        </div>
      </div>
      <div class="weather-item">
        <span class="weather-icon">💦</span>
        <div class="weather-detail">
          <span class="weather-label">Humidity</span>
          <span class="weather-value">{{ weather.humidity }}%</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Weather } from '~/composables/useApi'

interface Props {
  weather: Weather
}

defineProps<Props>()
</script>

<style scoped>
.weather-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left: 4px solid #3b82f6;
  padding: 1.5rem;
  transition: border-color 0.2s ease;
}

.weather-card:hover {
  border-color: #3b82f6;
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
  align-items: center;
  gap: 0.75rem;
}

.header-title {
  font-size: 1.125rem;
  font-weight: 700;
  color: #3b82f6;
  margin: 0;
}

.data-badge {
  padding: 0.125rem 0.5rem;
  border-radius: 9999px;
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.data-badge.historical {
  background: rgba(107, 114, 128, 0.15);
  color: var(--color-muted);
}

.data-badge.forecast {
  background: rgba(59, 130, 246, 0.15);
  color: #3b82f6;
}

.weather-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem;
}

.weather-item {
  display: flex;
  align-items: flex-start;
  gap: 0.625rem;
}

.weather-icon {
  font-size: 1.25rem;
  flex-shrink: 0;
  line-height: 1;
  margin-top: 0.125rem;
}

.weather-detail {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.weather-label {
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-muted);
}

.weather-value {
  font-weight: 600;
  font-size: 0.9375rem;
}

/* Mobile */
@media (max-width: 640px) {
  .weather-card {
    padding: 1rem;
  }

  .weather-grid {
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }

  .header-title {
    font-size: 1rem;
  }

  .weather-value {
    font-size: 0.875rem;
  }
}
</style>
