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
