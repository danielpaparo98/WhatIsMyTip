<template>
  <section class="hero">
    <h1>About</h1>
    <p>How WhatIsMyTip uses AI to help you make better footy tips.</p>
  </section>

  <section class="section">
        <h2>Our Approach</h2>
        <div class="content">
          <p>
            WhatIsMyTip combines eight machine learning and statistical models with three heuristic
            strategies to generate footy tips. Our system analyzes historical results, team and player
            form, head-to-head matchups, home advantage, injuries, weather, and value opportunities to
            provide informed predictions.
          </p>
          <p>
            We use a large language model via
            <a href="https://openrouter.ai" target="_blank" rel="noopener">OpenRouter</a>
            to generate human-readable explanations for each tip, helping you understand the reasoning
            behind our predictions. The model is configurable, so we can swap in newer options as they
            become available.
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
          <div class="model-card">
            <h3>Weather Impact</h3>
            <p>
              Weather can dramatically change how a game of AFL is played. This model classifies the
              forecast into tiers — from clear conditions to heavy rain and wind — then compares each
              team's historical win rate in similar conditions to predict who copes best.
            </p>
            <ul>
              <li>Uses match-day weather forecasts</li>
              <li>Accounts for rain, wind and temperature</li>
              <li>Best for games played in extreme conditions</li>
            </ul>
          </div>
          <div class="model-card">
            <h3>Injury Impact</h3>
            <p>
              Missing key players can swing a result. This model pulls each team's current injury list,
              weighs every absent player's importance using their recent statistics, and penalizes the
              side carrying the heavier injury burden.
            </p>
            <ul>
              <li>Quantifies the value of injured players</li>
              <li>Updates as injury lists change</li>
              <li>Best when star players are ruled out</li>
            </ul>
          </div>
          <div class="model-card">
            <h3>Matchup</h3>
            <p>
              Some teams just have another's number. This model studies the head-to-head history between
              the two teams, blending overall matchup results with how each side performs at the venue —
              all weighted so recent meetings count for more.
            </p>
            <ul>
              <li>Combines head-to-head history (60%) with venue record (40%)</li>
              <li>Applies time decay to favour recent clashes</li>
              <li>Best for long-standing rivalries</li>
            </ul>
          </div>
          <div class="model-card">
            <h3>Player Form</h3>
            <p>
              Rather than looking at team results, this model dives into individual performances. It
              aggregates advanced stats — score involvements, contested possessions, metres gained and
              pressure acts — over each team's last few games to gauge who is genuinely playing the
              better football right now.
            </p>
            <ul>
              <li>Measures team quality from player-level data</li>
              <li>Focuses on advanced performance metrics</li>
              <li>Best for spotting teams in peak form</li>
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
            Match fixtures, results and team data come from the <a href="https://squiggle.com.au" target="_blank" rel="noopener">Squiggle</a> API — an excellent resource for AFL statistics that makes this project possible. Historical player statistics and injury lists are sourced from AFL Tables and FootyWire, and match-day weather forecasts come from Open-Meteo.
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
// FX-05 / FX-20: page-specific SEO + canonical URL
useSeoMeta({
  title: 'About',
  description: 'Learn about WhatIsMyTip\'s AI-powered AFL tipping system. Discover our machine learning models, heuristics, and how we generate accurate AFL predictions and betting tips.',
  keywords: 'AFL tipping models, AFL prediction algorithms, machine learning AFL, AFL betting models, Elo ratings AFL, AFL form analysis, AFL home advantage, AFL injury impact, AFL weather predictions, AFL head-to-head predictions, AFL player form',
  ogTitle: 'About | WhatIsMyTip - AI-Powered AFL Tipping',
  ogDescription: 'Learn about WhatIsMyTip\'s AI-powered AFL tipping system. Discover our machine learning models and heuristics.',
  ogType: 'website',
  ogUrl: 'https://whatismytip.com/about',
  twitterTitle: 'About | WhatIsMyTip - AI-Powered AFL Tipping',
  twitterDescription: 'Learn about WhatIsMyTip\'s AI-powered AFL tipping system. Discover our machine learning models and heuristics.',
  twitterCard: 'summary_large_image',
})

useHead({
  link: [
    { rel: 'canonical', href: 'https://whatismytip.com/about' }
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
