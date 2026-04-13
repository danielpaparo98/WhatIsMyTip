// https://nuxt.com/docs/api/configuration/nuxt-config
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
        { name: 'theme-color', content: '#1e3a5f' },
        
        // Open Graph / Facebook
        { property: 'og:type', content: 'website' },
        { property: 'og:site_name', content: 'WhatIsMyTip' },
        { property: 'og:description', content: 'Get AI-powered AFL tips and predictions with smart heuristics. Expert footy tipping advice, betting tips, and round predictions backed by machine learning models.' },
        { property: 'og:url', content: 'https://whatismytip.com' },
        // TODO: Create og-image.png (1200x630) and uncomment the following lines
        // { property: 'og:image', content: 'https://whatismytip.com/og-image.png' },
        // { property: 'og:image:width', content: '1200' },
        // { property: 'og:image:height', content: '630' },
        // { property: 'og:image:alt', content: 'WhatIsMyTip - AI-Powered AFL Tipping' },
        
        // Twitter Card
        { name: 'twitter:card', content: 'summary_large_image' },
        { name: 'twitter:description', content: 'Get AI-powered AFL tips and predictions with smart heuristics. Expert footy tipping advice, betting tips, and round predictions.' },
        // TODO: Create twitter-card.png and uncomment the following line
        // { name: 'twitter:image', content: 'https://whatismytip.com/twitter-card.png' },
        // { name: 'twitter:image:alt', content: 'WhatIsMyTip - AI-Powered AFL Tipping' }
      ],
      link: [
        // TODO: Create favicon.ico and uncomment the following line
        // { rel: 'icon', type: 'image/x-icon', href: '/favicon.ico' },
        { rel: 'canonical', href: 'https://whatismytip.com' }
      ],
      script: [
        {
          src: process.env.UMAMI_HOST ? `${process.env.UMAMI_HOST}/script.js` : '',
          'data-website-id': process.env.UMAMI_WEBSITE_ID || '',
          defer: true,
          key: 'umami-analytics'
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
            logo: 'https://whatismytip.com/logo.png',
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
      apiBase: process.env.API_BASE_URL || 'http://localhost:8000',
      umamiHost: process.env.UMAMI_HOST || '',
      umamiWebsiteId: process.env.UMAMI_WEBSITE_ID || '',
      siteUrl: process.env.SITE_URL || 'https://whatismytip.com',
      buyMeACoffeeUrl: process.env.NUXT_PUBLIC_BUY_ME_A_COFFEE_URL || ''
    }
  },
  
  nitro: {
    preset: 'static'
  }
})
