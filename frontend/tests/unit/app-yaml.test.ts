/**
 * Linter for `.do/app.yaml`.
 *
 * Catches the most common foot-guns before they hit main:
 *   • A `type: SECRET` entry that accidentally has a real value
 *     (e.g. someone copy-pastes from .env into the spec).
 *   • A `value:` next to a SECRET key that *looks* like a connection
 *     string / API key (postgres://, redis://, sk-or-, AVNS_, xoxb-,
 *     ghp_, AKIA, https://hooks.slack.com, etc).
 *   • A backend key the FastAPI app actually reads (per
 *     packages/shared/config.py) that's missing from the spec.
 *   • A frontend key Nuxt reads at build time that's missing.
 *   • Structural drift: missing components, wrong routes, the api
 *     component accidentally given a public route, etc.
 *
 * The committed `app.yaml` MUST be safe to publish — this test is
 * the gate that keeps it that way.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
// frontend/ lives one directory below the repo root (../ = repo root).
const REPO_ROOT = resolve(FRONTEND_ROOT, '..')
const SPEC = resolve(REPO_ROOT, '.do/app.yaml')
const BOOTSTRAP_SH = resolve(REPO_ROOT, 'scripts/setup-app-secrets.sh')
const BOOTSTRAP_PS1 = resolve(REPO_ROOT, 'scripts/setup-app-secrets.ps1')

const SECRET_KEYS = [
  'DATABASE_URL',
  'REDIS_URL',
  'ADMIN_API_KEY',
  'OPENROUTER_API_KEY',
  'ALERT_WEBHOOK_URL',
]

const BACKEND_ENV_KEYS = [
  'ENVIRONMENT',
  'CORS_ORIGINS',
  'LOG_FORMAT',
  'DATABASE_URL',
  'DB_SSL_VERIFY',
  'DB_POOL_SIZE',
  'DB_MAX_OVERFLOW',
  'DB_POOL_TIMEOUT',
  'REDIS_URL',
  'ADMIN_API_KEY',
  'OPENROUTER_API_KEY',
  'OPENROUTER_BASE_URL',
  'OPENROUTER_MODEL',
  'SQUIGGLE_API_BASE',
  'SQUIGGLE_CONTACT_EMAIL',
  'RATE_LIMIT_PER_MINUTE',
  'RATE_LIMIT_MAX_REQUESTS',
  'RATE_LIMIT_WINDOW_SECONDS',
  'MAX_REQUEST_BODY_BYTES',
  'CRON_ENABLED',
  'CRON_TIMEZONE',
  'DAILY_SYNC_CRON',
  'MATCH_COMPLETION_CRON',
  'TIP_GENERATION_CRON',
  'HISTORIC_REFRESH_CRON',
  'DAILY_SYNC_OFF_SEASON_START_HOUR',
  'DAILY_SYNC_OFF_SEASON_END_HOUR',
  'CURRENT_SEASON',
  'HISTORIC_REFRESH_SEASONS',
  'HISTORICAL_REFRESH_START_YEAR',
  'DAILY_SYNC_ENABLED',
  'DAILY_SYNC_TIMEOUT_SECONDS',
  'MATCH_COMPLETION_BUFFER_MINUTES',
  'MATCH_COMPLETION_CHECK_ENABLED',
  'COMPLETION_CHECK_TIMEOUT_SECONDS',
  'TIP_GENERATION_ENABLED',
  'TIP_GENERATION_TIMEOUT_SECONDS',
  'TIP_GENERATION_REGENERATE_EXISTING',
  'HISTORIC_REFRESH_ENABLED',
  'HISTORIC_REFRESH_REGENERATE_TIPS',
  'HISTORICAL_REFRESH_TIMEOUT_SECONDS',
  'JOB_TIMEOUT_SECONDS',
  'JOB_LOCK_EXPIRE_SECONDS',
  'JOB_MAX_RETRIES',
  'JOB_RETRY_DELAY_SECONDS',
  'ALERT_ENABLED',
  'ALERT_WEBHOOK_URL',
  'ALERT_TIMEOUT_SECONDS',
  'METRICS_ENABLED',
  'METRICS_RETENTION_DAYS',
  'FORWARDED_ALLOW_IPS',
]

const FRONTEND_ENV_KEYS = [
  'NUXT_PUBLIC_API_BASE',
  'NUXT_PUBLIC_SITE_URL',
  'NUXT_PUBLIC_UMAMI_HOST',
  'NUXT_PUBLIC_UMAMI_WEBSITE_ID',
  'NUXT_PUBLIC_BUY_ME_A_COFFEE_URL',
  'NODE_ENV',
]

describe('.do/app.yaml spec', () => {
  // Normalize CRLF (Windows-checked-out file) to LF so regex / split
  // anchors work uniformly across platforms.
  const raw = existsSync(SPEC) ? readFileSync(SPEC, 'utf8') : ''
  const spec = raw.replace(/\r\n/g, '\n')

  it('exists', () => {
    expect(spec, 'missing .do/app.yaml').not.toBe('')
  })

  it('declares the two components with the expected names', () => {
    expect(spec).toMatch(/-\s+name:\s*whatismytip-api\b/)
    expect(spec).toMatch(/-\s+name:\s*whatismytip-frontend\b/)
    // No reverse proxy is used — the api is exposed directly at /api.
    expect(spec).not.toMatch(/-\s+name:\s*whatismytip-proxy\b/)
  })

  it('routes /api (plus /health and the docs) to the api, and / to the frontend', () => {
    // Tolerate CRLF (Windows) and LF (Unix) line endings.
    expect(spec).toMatch(/-\s+path:\s*\/api\s*(?:\r?\n|$)/)
    expect(spec).toMatch(/-\s+path:\s*\/health\s*(?:\r?\n|$)/)
    expect(spec).toMatch(/-\s+path:\s*\/docs\s*(?:\r?\n|$)/)
    expect(spec).toMatch(/-\s+path:\s*\/openapi\.json\s*(?:\r?\n|$)/)
    expect(spec).toMatch(/-\s+path:\s*\/\s*(?:\r?\n|$)/)
  })

  it('declares a github source spec on every component (doctl requires it)', () => {
    // App Platform needs an explicit `github:` (or `image:`) block on
    // every component, otherwise doctl rejects the spec with
    // "service ... missing source spec (image, git, github, gitlab
    // or bitbucket)".
    const apiBlock = spec.match(
      /-\s+name:\s*whatismytip-api[\s\S]*?(?=\n\s*-\s+name:|\nstatic_sites:|\s*$)/,
    )
    const frontendBlock = spec.match(
      /-\s+name:\s*whatismytip-frontend[\s\S]*?(?=\n\s*-\s+name:|\s*$)/,
    )
    expect(apiBlock, 'api service block not found').not.toBeNull()
    expect(frontendBlock, 'frontend block not found').not.toBeNull()
    for (const [name, block] of [['api', apiBlock![0]], ['frontend', frontendBlock![0]]]) {
      expect(block, `${name} needs a github: source block`).toMatch(/\bgithub:\s*\n/)
      expect(block, `${name} github block needs a 'repo:'`).toMatch(/\brepo:\s*\S/)
      expect(block, `${name} github block needs a 'branch:'`).toMatch(/\bbranch:\s*\S/)
    }
  })

  it('puts all /api* routes inside the api component (not the frontend)', () => {
    // Find the api service block and assert it contains the /api route.
    const apiBlock = spec.match(
      /-\s+name:\s*whatismytip-api[\s\S]*?(?=\n\s*-\s+name:|\nstatic_sites:|\s*$)/,
    )
    expect(apiBlock, 'api service block not found').not.toBeNull()
    expect(apiBlock![0]).toMatch(/-\s+path:\s*\/api\s*(?:\r?\n|$)/)

    // Find the frontend block and assert it does NOT claim /api.
    const frontendBlock = spec.match(
      /-\s+name:\s*whatismytip-frontend[\s\S]*?(?=\n\s*-\s+name:|\s*$)/,
    )
    expect(frontendBlock, 'frontend block not found').not.toBeNull()
    expect(frontendBlock![0]).toMatch(/-\s+path:\s*\/\s*(?:\r?\n|$)/)
  })

  it('declares every backend env var the FastAPI app reads', () => {
    const missing = BACKEND_ENV_KEYS.filter((k) => !spec.includes(`- key: ${k}`))
    expect(
      missing,
      `these backend env keys are missing from app.yaml: ${missing.join(', ')}`,
    ).toEqual([])
  })

  it('declares every frontend env var the Nuxt build reads', () => {
    const missing = FRONTEND_ENV_KEYS.filter((k) => !spec.includes(`- key: ${k}`))
    expect(
      missing,
      `these frontend env keys are missing from app.yaml: ${missing.join(', ')}`,
    ).toEqual([])
  })

  it('marks every secret key as type: SECRET (and never as a plain value)', () => {
    // For each secret key, locate its `- key: NAME` line, then read
    // forward ONLY to the next `- key:` (exclusive) so we don't
    // accidentally inspect the next env entry's value.
    const lines = spec.split('\n')
    for (const key of SECRET_KEYS) {
      const idx = lines.findIndex((l) => l.trim() === `- key: ${key}`)
      expect(idx, `no '- key: ${key}' line found in spec`).toBeGreaterThanOrEqual(0)
      let end = lines.length
      for (let j = idx + 1; j < lines.length; j++) {
        const ln = lines[j]
        if (ln !== undefined && ln.trim().startsWith('- key:')) {
          end = j
          break
        }
      }
      const block = lines.slice(idx, end).join('\n')
      expect(
        block,
        `${key} must be declared as 'type: SECRET' (no inline value)`,
      ).toMatch(/type:\s*SECRET/)
      expect(
        block,
        `${key} must NOT carry an inline 'value:' — it is populated via setup-app-secrets.sh`,
      ).not.toMatch(/\bvalue:\s*\S/)
    }
  })

  it('never contains plaintext values that look like real secrets', () => {
    // Heuristic patterns that should NEVER appear outside of comments.
    // If any of these match, the spec has been contaminated.
    const SUSPICIOUS = [
      // Postgres / Redis connection strings (look for credentials)
      /postgresql\+\?asyncpg:\/\/[^"'\s]+\:[^"'\s]+@/i,
      /postgres:\/\/[^"'\s]+\:[^"'\s]+@/i,
      /rediss?:\/\/[^"'\s]+\:[^"'\s]+@/i,
      // DO managed-DB password prefixes
      /\bAVNS_[A-Za-z0-9_-]+/,
      // OpenRouter / GitHub PAT / Slack / AWS prefixes
      /\bsk-or-v1-[A-Za-z0-9]{16,}/,
      /\bghp_[A-Za-z0-9]{16,}/,
      /\bxox[baprs]-[A-Za-z0-9-]{10,}/,
      /\bAKIA[0-9A-Z]{16}/,
      // 48+ char URL-safe random strings (ADMIN_API_KEY style)
      /\b[A-Za-z0-9_-]{48,}\b/,
      // Slack/Discord webhook URLs with a token
      /hooks\.slack\.com\/services\/T[A-Z0-9]+\/B[A-Z0-9]+\/[A-Za-z0-9]+/,
    ]

    const hits = SUSPICIOUS.filter((re) => re.test(spec))
    expect(
      hits,
      `app.yaml contains ${hits.length} suspicious secret-like pattern(s); ` +
        'this file is committed to git and must not contain real credentials',
    ).toEqual([])
  })

  it('ships a bootstrap script (sh + ps1) so secrets can be populated', () => {
    expect(existsSync(BOOTSTRAP_SH), 'scripts/setup-app-secrets.sh missing').toBe(true)
    expect(existsSync(BOOTSTRAP_PS1), 'scripts/setup-app-secrets.ps1 missing').toBe(true)
    const sh = readFileSync(BOOTSTRAP_SH, 'utf8')
    expect(sh).toMatch(/^#!\/usr\/bin\/env bash/)
    // The required-secret list inside the script must match the spec
    for (const key of SECRET_KEYS) {
      expect(sh, `setup-app-secrets.sh does not reference ${key}`).toContain(key)
    }
  })
})