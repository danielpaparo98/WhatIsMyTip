// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2024-04-03',
  devtools: { enabled: true },
  
  modules: ['@nuxtjs/tailwindcss'],
  
  app: {
    head: {
      title: 'WhatIsMyTip.com',
      meta: [
        { charset: 'utf-8' },
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        { name: 'description', content: 'AI-powered footy tipping with smart heuristics' },
        { name: 'robots', content: 'index, follow' },
        { property: 'og:title', content: 'WhatIsMyTip.com' },
        { property: 'og:description', content: 'AI-powered footy tipping with smart heuristics' },
        { property: 'og:type', content: 'website' }
      ],
      link: [
        { rel: 'icon', type: 'image/x-icon', href: '/favicon.ico' }
      ],
      script: [
        {
          src: process.env.UMAMI_HOST ? `${process.env.UMAMI_HOST}/script.js` : '',
          'data-website-id': process.env.UMAMI_WEBSITE_ID || '',
          defer: true,
          key: 'umami-analytics'
        }
      ]
    }
  },
  
  css: ['~/assets/css/main.css'],
  
  runtimeConfig: {
    public: {
      apiBase: process.env.API_BASE_URL || 'http://localhost:8000',
      umamiHost: process.env.UMAMI_HOST || '',
      umamiWebsiteId: process.env.UMAMI_WEBSITE_ID || ''
    }
  },
  
  nitro: {
    preset: 'static'
  }
})
