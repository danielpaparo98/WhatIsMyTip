/**
 * FX-02: Chart performance — chart components must use `computed()` for
 * `chartData` so the chart re-renders only when the `data` prop changes,
 * not on every parent re-render.
 *
 * This is a source-grep test because the win is "vue-chartjs receives
 * a stable reactive reference and doesn't have to re-instantiate Chart.js
 * on every render of the parent page".  Verifying this at runtime would
 * require a full Chart.js + happy-dom canvas stack.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const CHART_FILES = [
  'components/ProfitChart.vue',
  'components/AccuracyChart.vue',
  'components/CumulativeProfitChart.vue',
]

describe('FX-02: chart components use computed for chartData', () => {
  for (const rel of CHART_FILES) {
    describe(rel, () => {
      const source = readFileSync(resolve(FRONTEND_ROOT, rel), 'utf8')

      it('declares chartData as a computed()', () => {
        // Match: const chartData = computed(...)
        const re = /const\s+chartData\s*=\s*computed\s*\(/
        expect(source.match(re), 'chartData should be defined via computed()').not.toBeNull()
      })

      it('does not re-instantiate chartData on every render (no plain object literal in <Line/Bar :data="...">)', () => {
        // Anti-pattern: <Line :data="{ labels: [], datasets: [] }" />
        // Acceptable: <Line :data="chartData" />
        const inline = /<(Line|Bar)\b[^>]*\b:data\s*=\s*"\{\s*labels/
        expect(source.match(inline), 'do not pass an inline object literal as :data').toBeNull()
      })
    })
  }
})
