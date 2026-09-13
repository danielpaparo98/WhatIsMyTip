/**
 * Conditional Umami analytics registration (client-only).
 *
 * FIX H-1 (2026-09 review): the analytics script used to be declared
 * unconditionally in nuxt.config.ts `app.head.script` with empty
 * strings when NUXT_PUBLIC_UMAMI_HOST / _WEBSITE_ID were unset.  An
 * empty `src` resolves to the *current document URL*, so the browser
 * fetched the page's own HTML and tried to execute it as JavaScript —
 * a console error and a wasted request on every page load.
 *
 * Now the script is only injected when both env vars are configured.
 * Set (at build time):
 *   NUXT_PUBLIC_UMAMI_HOST=e.g. https://analytics.example.com
 *   NUXT_PUBLIC_UMAMI_WEBSITE_ID=e.g. 00000000-0000-0000-0000-000000000000
 */
export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig()
  const host = config.public.umamiHost as string
  const websiteId = config.public.umamiWebsiteId as string

  if (!host || !websiteId) return

  if (!/^https?:\/\//.test(host)) {
    if (import.meta.dev) {
      console.warn('[umami] NUXT_PUBLIC_UMAMI_HOST must be an absolute URL — analytics disabled')
    }
    return
  }

  useHead({
    script: [
      {
        src: `${host}/script.js`,
        'data-website-id': websiteId,
        defer: true,
        key: 'umami-analytics',
      },
    ],
  })
})
