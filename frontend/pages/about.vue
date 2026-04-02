<template>
  <section class="hero">
    <h1>About</h1>
    <p>How WhatIsMyTip uses AI to help you make better footy tips.</p>
  </section>

  <section class="section">
        <h2>Our Approach</h2>
        <div class="content">
          <p>
            WhatIsMyTip combines multiple machine learning and financial models with heuristic strategies
            to generate footy tips. Our system analyzes historical data, team form, home advantage,
            and value opportunities to provide informed predictions.
          </p>
          <p>
            We use OpenRouter's gptoss-120b model to generate human-readable explanations for each tip,
            helping you understand the reasoning behind our predictions.
          </p>
    </div>
  </section>

  <section class="section">
        <h2>Models</h2>
        <div class="models">
          <div class="model-card">
            <h3>Elo</h3>
            <p>
              A rating system that tracks team strength based on historical performance. Teams gain points for winning and lose points for losing, with the amount adjusted based on the opponent's strength.
            </p>
            <ul>
              <li>Adapts to team strength over time</li>
              <li>Accounts for home advantage</li>
              <li>Best for measuring long-term team quality</li>
            </ul>
          </div>
          <div class="model-card">
            <h3>Form</h3>
            <p>
              Analyzes recent team performance over the last few games to identify current momentum. This model looks at recent wins, losses, and scoring margins to determine which team is currently playing better.
            </p>
            <ul>
              <li>Focuses on short-term trends</li>
              <li>Captures team momentum</li>
              <li>Best for teams in good or poor form</li>
            </ul>
          </div>
          <div class="model-card">
            <h3>Home Advantage</h3>
            <p>
              A simple but effective model that leverages the historical tendency for home teams to win more often. It analyzes venue-specific win rates to predict outcomes based on where the game is played.
            </p>
            <ul>
              <li>Uses venue-specific statistics</li>
              <li>Simple and reliable</li>
              <li>Best for games at strong home venues</li>
            </ul>
          </div>
          <div class="model-card">
            <h3>Value</h3>
            <p>
              Identifies undervalued teams by comparing historical win rates against perceived odds. This model looks for teams that perform better than their reputation suggests, finding value opportunities.
            </p>
            <ul>
              <li>Targets undervalued teams</li>
              <li>Uses long-term win rate data</li>
              <li>Best for finding upset opportunities</li>
            </ul>
          </div>
    </div>
  </section>

  <section class="section">
        <h2>Heuristics</h2>
        <div class="heuristics">
          <div class="heuristic-card">
            <h3>Best Bet</h3>
            <p>
              Conservative, consensus-based picks with high confidence. This heuristic aggregates
              predictions from all models and selects the most confident consensus pick.
            </p>
          </div>
          <div class="heuristic-card">
            <h3>YOLO</h3>
            <p>
              Aggressive picks based on the highest confidence model prediction. This heuristic
              goes all-in on the model with the strongest conviction.
            </p>
          </div>
          <div class="heuristic-card">
            <h3>High Risk / High Reward</h3>
            <p>
              Targets upset opportunities when models disagree. This heuristic identifies games
              with high variance and picks the underdog for potentially higher returns.
            </p>
          </div>
    </div>
  </section>

  <section class="section">
        <h2>Data Source</h2>
        <div class="content">
          <p>
            All match data is sourced from <a href="https://squiggle.com.au" target="_blank" rel="noopener">Squiggle</a>,
            an excellent resource for AFL statistics and historical data. We're grateful for their
            public API which makes this project possible.
          </p>
    </div>
  </section>

  <section class="section">
        <h2>Open Source</h2>
        <div class="content">
          <p>
            WhatIsMyTip is an open source project. The code is available on GitHub and we welcome
            contributions from the community.
          </p>
    </div>
  </section>
</template>

<script setup lang="ts">
definePageMeta({
  layout: 'default'
})

// Page-specific SEO
useHead({
  title: 'About | WhatIsMyTip - AI-Powered AFL Tipping',
  meta: [
    { name: 'description', content: 'Learn about WhatIsMyTip\'s AI-powered AFL tipping system. Discover our machine learning models, heuristics, and how we generate accurate AFL predictions and betting tips.' },
    { name: 'keywords', content: 'AFL tipping models, AFL prediction algorithms, machine learning AFL, AFL betting models, Elo ratings AFL, AFL form analysis, AFL home advantage' },
    { property: 'og:title', content: 'About | WhatIsMyTip - AI-Powered AFL Tipping' },
    { property: 'og:description', content: 'Learn about WhatIsMyTip\'s AI-powered AFL tipping system. Discover our machine learning models and heuristics.' },
    { property: 'og:url', content: 'https://whatismytip.com/about' },
    { name: 'twitter:title', content: 'About | WhatIsMyTip - AI-Powered AFL Tipping' },
    { name: 'twitter:description', content: 'Learn about WhatIsMyTip\'s AI-powered AFL tipping system. Discover our machine learning models and heuristics.' }
  ],
  script: [
    {
      type: 'application/ld+json',
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'AboutPage',
        name: 'About WhatIsMyTip',
        description: 'Learn about WhatIsMyTip\'s AI-powered AFL tipping system. Discover our machine learning models, heuristics, and how we generate accurate AFL predictions.',
        url: 'https://whatismytip.com/about',
        mainEntity: {
          '@type': 'SoftwareApplication',
          name: 'WhatIsMyTip',
          applicationCategory: 'SportsApplication',
          description: 'AI-powered AFL tips and predictions with smart heuristics'
        }
      })
    }
  ]
})
</script>

<style scoped>
.hero {
  padding: 3rem 1.5rem;
  text-align: center;
  max-width: 80ch;
  margin: 0 auto;
}

.hero h1 {
  font-size: clamp(2rem, 6vw, 4rem);
  margin-bottom: 1rem;
}

.section {
  padding: 3rem 1.5rem;
  max-width: 80ch;
  margin: 0 auto;
}

.section h2 {
  margin-bottom: 1.5rem;
}

.content {
  line-height: 1.8;
}

.models {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.model-card {
  border: 1px solid var(--color-border);
  padding: 1.5rem;
}

.model-card h3 {
  font-size: 1.25rem;
  margin-bottom: 0.875rem;
}

.model-card ul {
  margin-top: 0.875rem;
  padding-left: 1.25rem;
}

.model-card li {
  margin-bottom: 0.5rem;
}

.heuristics {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.heuristic-card {
  border: 1px solid var(--color-border);
  padding: 1.5rem;
}

.heuristic-card h3 {
  font-size: 1.25rem;
  margin-bottom: 0.875rem;
}

/* Mobile styles */
@media (max-width: 640px) {
  .hero {
    padding: 2.5rem 1rem;
  }

  .hero h1 {
    margin-bottom: 0.875rem;
  }

  .section {
    padding: 2rem 1rem;
  }

  .section h2 {
    margin-bottom: 1.25rem;
  }

  .model-card,
  .heuristic-card {
    padding: 1.25rem;
  }

  .model-card h3,
  .heuristic-card h3 {
    font-size: 1.125rem;
  }

  .model-card ul {
    padding-left: 1rem;
  }
}

/* Tablet styles */
@media (min-width: 641px) and (max-width: 1024px) {
  .models {
    gap: 2rem;
  }

  .heuristics {
    gap: 2rem;
  }
}

/* Desktop styles */
@media (min-width: 1025px) {
  .hero {
    padding: 4rem 2rem;
  }

  .hero h1 {
    font-size: clamp(2.5rem, 8vw, 4rem);
  }

  .section {
    padding: 4rem 2rem;
  }

  .section h2 {
    margin-bottom: 2rem;
  }

  .models {
    gap: 2rem;
  }

  .heuristics {
    gap: 2rem;
  }

  .model-card,
  .heuristic-card {
    padding: 2rem;
  }

  .model-card h3,
  .heuristic-card h3 {
    font-size: 1.5rem;
  }

  .model-card ul {
    padding-left: 1.5rem;
  }
}
</style>
