<template>
  <div>
    <Header />
    <main class="main">
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
    </main>
    <Footer />
  </div>
</template>

<style scoped>
.main {
  max-width: 800px;
  margin: 0 auto;
  padding: 2rem;
}

.hero {
  padding: 4rem 2rem;
  text-align: center;
}

.hero h1 {
  font-size: clamp(2.5rem, 8vw, 4rem);
  margin-bottom: 1rem;
}

.section {
  padding: 4rem 2rem;
}

.section h2 {
  margin-bottom: 2rem;
}

.content {
  line-height: 1.8;
}

.models {
  display: grid;
  gap: 2rem;
}

.model-card {
  border: 1px solid var(--color-border);
  padding: 2rem;
}

.model-card h3 {
  font-size: 1.5rem;
  margin-bottom: 1rem;
}

.model-card ul {
  margin-top: 1rem;
  padding-left: 1.5rem;
}

.model-card li {
  margin-bottom: 0.5rem;
}

.heuristics {
  display: grid;
  gap: 2rem;
}

.heuristic-card {
  border: 1px solid var(--color-border);
  padding: 2rem;
}

.heuristic-card h3 {
  font-size: 1.5rem;
  margin-bottom: 1rem;
}
</style>
