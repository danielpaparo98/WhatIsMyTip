<template>
  <div class="chart-container">
    <h3 class="chart-title">Accuracy Comparison</h3>
    <div v-if="loading" class="chart-loading">
      <div class="spinner"></div>
    </div>
    <div v-else-if="!hasData" class="chart-empty">
      <p>No data available</p>
    </div>
    <div v-else class="chart-wrapper">
      <Bar
        :data="chartData"
        :options="chartOptions"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
  type ChartOptions,
  type Plugin,
} from 'chart.js'
import { Bar } from 'vue-chartjs'
import { useChartTheme } from '~/composables/useChartTheme'

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend
)

interface RoundData {
  round_id: number
  accuracy: number
}

interface HeuristicData {
  heuristic: string
  rounds: RoundData[]
}

interface Props {
  data: HeuristicData[]
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false
})

const { getHeuristicColors, getHeuristicLabel } = useChartTheme()

const hasData = computed(() => {
  return props.data && props.data.length > 0 && props.data.some(h => h.rounds && h.rounds.length > 0)
})

const chartData = computed(() => {
  if (!hasData.value) return { labels: [], datasets: [] }

  // Get all unique round IDs sorted
  const allRounds = new Set<number>()
  props.data.forEach(h => {
    h.rounds.forEach(r => allRounds.add(r.round_id))
  })
  const sortedRounds = Array.from(allRounds).sort((a, b) => a - b)

  // Create datasets for each heuristic
  const datasets = props.data.map(heuristicData => {
    const colors = getHeuristicColors(heuristicData.heuristic, 0.8)

    const data = sortedRounds.map(roundId => {
      const roundData = heuristicData.rounds.find(r => r.round_id === roundId)
      return roundData ? roundData.accuracy * 100 : null
    })

    return {
      label: getHeuristicLabel(heuristicData.heuristic),
      data,
      backgroundColor: colors.background,
      borderColor: colors.border,
      borderWidth: 2,
      borderRadius: 4,
      barPercentage: 0.7,
      categoryPercentage: 0.8
    }
  })

  return {
    labels: sortedRounds.map(r => `R${r}`),
    datasets
  }
})

const chartOptions: ChartOptions<'bar'> = {
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
        font: {
          size: 12,
          weight: 600
        }
      }
    },
    tooltip: {
      backgroundColor: 'rgba(0, 0, 0, 0.8)',
      padding: 12,
      titleFont: {
        size: 14,
        weight: 700
      },
      bodyFont: {
        size: 13
      },
      callbacks: {
        label: (context) => {
          const value = context.parsed.y
          if (value === null) return ''
          const label = context.dataset.label || ''
          return `${label}: ${value.toFixed(1)}%`
        }
      }
    }
  },
  scales: {
    x: {
      title: {
        display: true,
        text: 'Round',
        font: {
          size: 12,
          weight: 600
        }
      },
      grid: {
        display: false
      }
    },
    y: {
      title: {
        display: true,
        text: 'Accuracy (%)',
        font: {
          size: 12,
          weight: 600
        }
      },
      min: 0,
      max: 100,
      ticks: {
        callback: (value) => `${value}%`
      },
      grid: {
        color: 'rgba(0, 0, 0, 0.05)'
      }
    }
  }
}

const plugins: Plugin<'bar'>[] = []
</script>

<style scoped>
.chart-container {
  border: 1px solid var(--color-border);
  padding: 1.25rem;
  border-radius: 0.5rem;
}

.chart-title {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 0.875rem;
  text-align: center;
}

.chart-wrapper {
  position: relative;
  height: 350px;
  width: 100%;
}

.chart-loading,
.chart-empty {
  height: 350px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.spinner {
  width: 36px;
  height: 36px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-text);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.chart-empty p {
  color: var(--color-muted);
  font-style: italic;
}

/* Mobile styles */
@media (max-width: 640px) {
  .chart-container {
    padding: 1rem;
  }

  .chart-title {
    font-size: 0.9375rem;
    margin-bottom: 0.75rem;
  }

  .chart-wrapper {
    height: 400px;
  }

  .chart-loading,
  .chart-empty {
    height: 400px;
  }

  .spinner {
    width: 32px;
    height: 32px;
  }
}

/* Tablet styles */
@media (min-width: 641px) and (max-width: 1024px) {
  .chart-wrapper {
    height: 350px;
  }

  .chart-loading,
  .chart-empty {
    height: 350px;
  }
}

/* Desktop styles */
@media (min-width: 1025px) {
  .chart-container {
    padding: 1.5rem;
  }

  .chart-title {
    font-size: 1.125rem;
    margin-bottom: 1rem;
  }

  .chart-wrapper {
    height: 400px;
  }

  .chart-loading,
  .chart-empty {
    height: 400px;
  }

  .spinner {
    width: 40px;
    height: 40px;
  }
}
</style>
