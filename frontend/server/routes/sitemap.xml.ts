/**
 * Build-time sitemap generation (FIX M-6, 2026-09 review).
 *
 * The previous `public/sitemap.xml` was a static file last updated
 * 2025-03-31 with no game URLs.  This server route is prerendered by
 * Nitro (see `nitro.prerender.routes` in nuxt.config.ts): at build time
 * it queries the games API for the current round's games and emits a
 * fresh sitemap including /game/{slug} entries.
 *
 * Graceful degradation: when the API is unreachable at build time the
 * sitemap still renders with the static pages.
 */

interface BuildLatestRound {
  season: number | null
  round_id: number | null
}

interface BuildGame {
  slug: string
  date: string | null
}

const SITE_URL = process.env.NUXT_PUBLIC_SITE_URL || 'https://whatismytip.com'
const API_BASE = process.env.NUXT_PUBLIC_API_BASE || 'http://localhost:8000'

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function buildUrlEntry(path: string, lastmod?: string | null): string {
  const mod = lastmod ? `<lastmod>${escapeXml(lastmod)}</lastmod>` : ''
  return `  <url>\n    <loc>${escapeXml(`${SITE_URL}${path}`)}</loc>\n    ${mod}<changefreq>daily</changefreq>\n  </url>`
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`)
  return (await res.json()) as T
}

export default defineEventHandler(async (event) => {
  const urls: string[] = [
    buildUrlEntry('/'),
    buildUrlEntry('/about'),
    buildUrlEntry('/backtest'),
  ]

  try {
    const latest = await fetchJson<BuildLatestRound>(
      `${API_BASE}/api/games?latest=true`,
    )
    if (latest?.season && latest?.round_id) {
      const payload = await fetchJson<{ games?: BuildGame[] }>(
        `${API_BASE}/api/tips/games-with-tips?season=${latest.season}&round=${latest.round_id}&heuristic=best_bet`,
      )
      for (const game of payload.games ?? []) {
        if (typeof game.slug === 'string' && game.slug.length > 0) {
          urls.push(buildUrlEntry(`/game/${game.slug}`, game.date))
        }
      }
    }
  } catch {
    // API unreachable at build time — ship the static pages only.
  }

  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls.join('\n')}\n</urlset>\n`

  setResponseHeader(event, 'content-type', 'application/xml; charset=utf-8')
  return xml
})
