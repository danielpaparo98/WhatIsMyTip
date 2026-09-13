// https://nuxt.com/docs/api/configuration/nuxt-config

// Build-time API base (defaults to the local dev backend).  Used by the
// nitro prerender hook below to enumerate /game/{slug} routes so game
// pages are prerendered with inlined data instead of being
// client-only.  SEO-C1 (2026-09 review): without this, the prerendered
// site had zero tips content and no game pages at all.
const BUILD_API_BASE = process.env.NUXT_PUBLIC_API_BASE || 'http://localhost:8000'

/** Latest-round locator returned by `GET /api/games?latest=true`. */
interface BuildLatestRound {
  season: number | null
  round_id: number | null
}

/** Minimal game shape used for prerender route + sitemap enumeration. */
interface BuildGame {
  slug: string
}

export default defineNuxtConfig({
  compatibilityDate: '2024-04-03',
  devtools: { enabled: process.env.NODE_ENV !== 'production' },

  modules: ['@nuxtjs/tailwindcss'],

  app: {
    head: {
      titleTemplate: '%s | WhatIsMyTip',
      meta: [
        { charset: 'utf-8' },
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        { name: 'description', content: 'Get AI-powered AFL tips and predictions with smart heuristics. Expert footy tipping advice, betting tips, and round predictions backed by machine learning models.' },
        { name: 'robots', content: 'index, follow' },
        { name: 'author', content: 'WhatIsMyTip' },
        // FIX (L-1): theme-color now matches the monochrome design and
        // adapts to colour scheme (was a stray navy #1e3a5f).
        { name: 'theme-color', content: '#ffffff', media: '(prefers-color-scheme: light)' },
        { name: 'theme-color', content: '#121212', media: '(prefers-color-scheme: dark)' },

        // Open Graph / Facebook
        { property: 'og:type', content: 'website' },
        { property: 'og:site_name', content: 'WhatIsMyTip' },
        { property: 'og:description', content: 'Get AI-powered AFL tips and predictions with smart heuristics. Expert footy tipping advice, betting tips, and round predictions backed by machine learning models.' },
        { property: 'og:url', content: 'https://whatismytip.com' },
        // FIX (M-7): ship the social preview image (1200x630) so link
        // shares render the large card properly.
        { property: 'og:image', content: 'https://whatismytip.com/og-image.png' },
        { property: 'og:image:width', content: '1200' },
        { property: 'og:image:height', content: '630' },
        { property: 'og:image:alt', content: 'WhatIsMyTip - AI-Powered AFL Tipping' },

        // Twitter Card
        { name: 'twitter:card', content: 'summary_large_image' },
        { name: 'twitter:description', content: 'Get AI-powered AFL tips and predictions with smart heuristics. Expert footy tipping advice, betting tips, and round predictions.' },
        { name: 'twitter:image', content: 'https://whatismytip.com/og-image.png' },
        { name: 'twitter:image:alt', content: 'WhatIsMyTip - AI-Powered AFL Tipping' }
      ],
      link: [
        // FIX (H-2): the site-wide default canonical has been REMOVED.
        // Every page now declares its own canonical via useHead (using
        // runtimeConfig.public.siteUrl) — a global canonical pointing
        // at "/" contradicted the per-page canonicals on every
        // non-home page (duplicate-content hazard).
        //
        // FIX (M-8): the hardcoded cross-origin preconnect to the
        // retired API subdomain is gone — the production API is
        // same-origin (whatismytip.com/api), so that preconnect wasted
        // a connection on every page load.
        //
        // Analytics: the script is registered conditionally by
        // plugins/umami.client.ts (FIX H-1 — the old static entry
        // rendered <script src=""> with an empty src when the env vars
        // were unset, making the browser fetch the page itself as JS).
        { rel: 'icon', type: 'image/x-icon', href: '/favicon.ico' }
      ],
      script: [
        {
          innerHTML: '(function(){try{var m=localStorage.getItem("color-mode");if(m==="dark"||(!m&&window.matchMedia("(prefers-color-scheme:dark)").matches))document.documentElement.classList.add("dark")}catch(e){}})()'
        },
        {
          type: 'application/ld+json',
          innerHTML: JSON.stringify({
            '@context': 'https://schema.org',
            '@type': 'WebSite',
            name: 'WhatIsMyTip',
            url: 'https://whatismytip.com',
            description: 'AI-powered AFL tips and predictions with smart heuristics'
          })
        },
        {
          type: 'application/ld+json',
          innerHTML: JSON.stringify({
            '@context': 'https://schema.org',
            '@type': 'Organization',
            name: 'WhatIsMyTip',
            url: 'https://whatismytip.com',
            logo: 'https://whatismytip.com/og-image.png',
            description: 'AI-powered AFL tips and predictions with smart heuristics',
            sameAs: [
              'https://github.com/whatismytip'
            ]
          })
        }
      ]
    }
  },

  css: ['~/assets/css/main.css'],

  runtimeConfig: {
    public: {
      // Single FastAPI backend base URL (Phase 4: no more per-function
      // FaaS URLs — the FastAPI app exposes a single base URL and the
      // backend's /api/... routers handle the rest).  Set via
      // NUXT_PUBLIC_API_BASE at build time.
      //
      // All client-side env vars use the NUXT_PUBLIC_* prefix so they
      // are exposed to the browser by Nuxt's runtime config.  See
      // frontend/.env.example for the canonical list.  Fix CR-002/003.
      apiBase: process.env.NUXT_PUBLIC_API_BASE || 'http://localhost:8000',
      umamiHost: process.env.NUXT_PUBLIC_UMAMI_HOST || '',
      umamiWebsiteId: process.env.NUXT_PUBLIC_UMAMI_WEBSITE_ID || '',
      siteUrl: process.env.NUXT_PUBLIC_SITE_URL || 'https://whatismytip.com',
      buyMeACoffeeUrl: process.env.NUXT_PUBLIC_BUY_ME_A_COFFEE_URL || '',
    }
  },

  // NOTE: `nitro.preset = 'static'` means the build must run `nuxt generate`
  // (pre-render every route to HTML) — NOT `nuxt build` (SSR/Node server).
  // The `package.json` `build` script is therefore wired to `nuxt generate`
  // so `bun run build` actually produces the static site.  See Fix CR-001.
  nitro: {
    preset: 'static',
    prerender: {
      // crawlLinks picks up /game/{slug} links from the prerendered
      // index HTML (which now contains server-rendered tips data).
      crawlLinks: true,
      routes: ['/', '/about', '/backtest', '/sitemap.xml']
    }
  },

  // SEO-C1 (2026-09 review): enumerate the current round's game pages
  // explicitly so they are prerendered even if crawlLinks misses them,
  // and so the build degrades gracefully (skips game routes) when the
  // API is unreachable at build time.
  hooks: {
    'nitro:build:before': async (nitro) => {
      const routes = nitro.options.prerender.routes
      try {
        const latestRes = await fetch(`${BUILD_API_BASE}/api/games?latest=true`)
        if (!latestRes.ok) throw new Error(`latest round HTTP ${latestRes.status}`)
        const latest = (await latestRes.json()) as BuildLatestRound
        if (!latest?.season || !latest?.round_id) throw new Error('no active round')

        const gamesRes = await fetch(
          `${BUILD_API_BASE}/api/tips/games-with-tips?season=${latest.season}&round=${latest.round_id}&heuristic=best_bet`
        )
        if (!gamesRes.ok) throw new Error(`games HTTP ${gamesRes.status}`)
        const payload = (await gamesRes.json()) as { games?: BuildGame[] }

        const slugs = (payload.games ?? [])
          .map((g) => g.slug)
          .filter((slug): slug is string => typeof slug === 'string' && slug.length > 0)

        for (const slug of slugs) {
          if (!routes.includes(`/game/${slug}`)) routes.push(`/game/${slug}`)
        }
        nitro.logger.info(
          `prerender: added ${slugs.length} game routes for season ${latest.season} round ${latest.round_id}`
        )
      } catch (err) {
        // Graceful degradation: the site still builds (index/backtest/
        // about prerender with client-side fallbacks); game pages just
        // rely on the SPA fallback until the next successful build.
        nitro.logger.warn(
          `prerender: could not enumerate game routes (API unreachable at build time): ${err}`
        )
      }
    }
  }
})
