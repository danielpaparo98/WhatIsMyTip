<template>
  <div class="coeff-chart-container">
    <div v-if="!hasData" class="coeff-chart-empty">
      <p>No coefficient data available</p>
    </div>
    <div v-else class="coeff-chart-wrapper">
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
} from 'chart.js'
import { Bar } from 'vue-chartjs'
import { useColorMode } from '~/composables/useColorMode'

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
)

interface CoefficientRow {
  model: string
  margin_coef: number
  confidence_coef: number
}

interface Props {
  coefficients: CoefficientRow[]
  intercept: number
}

const props = defineProps<Props>()
const { isDark } = useColorMode()

const hasData = computed(() => props.coefficients.length > 0)

const chartData = computed(() => {
  // Sort by combined absolute influence descending
  const sorted = [...props.coefficients].sort(
    (a, b) => Math.abs(b.margin_coef + b.confidence_coef) - Math.abs(a.margin_coef + a.confidence_coef),
  )

  return {
    labels: sorted.map((r) => r.model),
    datasets: [
      {
        label: 'Margin Weight',
        data: sorted.map((r) => r.margin_coef),
        backgroundColor: sorted.map((r) =>
          r.margin_coef >= 0
            ? 'rgba(99, 102, 241, 0.75)'
            : 'rgba(239, 68, 68, 0.75)',
        ),
        borderColor: sorted.map((r) =>
          r.margin_coef >= 0
            ? 'rgba(99, 102, 241, 1)'
            : 'rgba(239, 68, 68, 1)',
        ),
        borderWidth: 1,
        borderRadius: 3,
      },
      {
        label: 'Confidence Weight',
        data: sorted.map((r) => r.confidence_coef),
        backgroundColor: sorted.map((r) =>
          r.confidence_coef >= 0
            ? 'rgba(16, 185, 129, 0.75)'
            : 'rgba(239, 68, 68, 0.75)',
        ),
        borderColor: sorted.map((r) =>
          r.confidence_coef >= 0
            ? 'rgba(16, 185, 129, 1)'
            : 'rgba(239, 68, 68, 1)',
        ),
        borderWidth: 1,
        borderRadius: 3,
      },
    ],
  }
})

const chartOptions = computed<ChartOptions<'bar'>>(() => ({
  indexAxis: 'y',
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      display: true,
      position: 'bottom',
      labels: {
        color: isDark.value ? '#e5e7eb' : '#374151',
        font: { size: 11, weight: '600' },
        padding: 16,
        usePointStyle: true,
        pointStyle: 'rectRounded',
      },
    },
    tooltip: {
      callbacks: {
        label: (ctx) => {
          const val = ctx.parsed.x
          return `${ctx.dataset.label}: ${val >= 0 ? '+' : ''}${val.toFixed(4)}`
        },
      },
    },
  },
  scales: {
    x: {
      title: {
        display: true,
        text: 'Coefficient Weight',
        color: isDark.value ? '#9ca3af' : '#6b7280',
        font: { size: 11, weight: '600' },
      },
      grid: {
        color: isDark.value ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
      },
      ticks: {
        color: isDark.value ? '#9ca3af' : '#6b7280',
        font: { size: 10 },
        callback: (val) => Number(val).toFixed(2),
      },
    },
    y: {
      grid: { display: false },
      ticks: {
        color: isDark.value ? '#e5e7eb' : '#374151',
        font: { size: 11, weight: '600' },
      },
    },
  },
}))
</script>

<style scoped>
.coeff-chart-container {
  width: 100%;
}

.coeff-chart-wrapper {
  height: 280px;
  position: relative;
}

.coeff-chart-empty {
  text-align: center;
  padding: 2rem 1rem;
  color: var(--color-muted);
  font-size: 0.875rem;
}
</style>
