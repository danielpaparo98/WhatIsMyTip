/**
 * FX-04: Bundle bloat — Chart.js is large, so we must avoid `chart.js/auto`
 * (which would pull the entire library).  Each chart component must import
 * only the scales/controllers it needs from `chart.js` directly so Vite can
 * tree-shake unused code.
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

describe('FX-04: Chart.js tree-shaking', () => {
  // Scan every .vue and .ts file for the forbidden import
  const filesToScan = [
    ...CHART_FILES,
    'composables/useChartTheme.ts',
    'composables/useApi.ts',
  ]

  it('no source file imports from "chart.js/auto"', () => {
    for (const rel of filesToScan) {
      const source = readFileSync(resolve(FRONTEND_ROOT, rel), 'utf8')
      const bad = /from\s+['"]chart\.js\/auto['"]/
      expect(
        source.match(bad),
        `${rel} must not import from chart.js/auto`,
      ).toBeNull()
    }
  })

  for (const rel of CHART_FILES) {
    describe(rel, () => {
      const source = readFileSync(resolve(FRONTEND_ROOT, rel), 'utf8')

      it('imports Chart as ChartJS from chart.js (named import)', () => {
        expect(source).toMatch(/from\s+['"]chart\.js['"]/)
      })

      it('calls ChartJS.register(...) to register the scales it uses', () => {
        expect(source).toMatch(/ChartJS\.register\s*\(/)
      })
    })
  }
})
