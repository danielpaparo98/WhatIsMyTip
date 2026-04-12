<template>
  <footer class="footer">
    <div class="footer-content">
      <p class="text-muted">
        Data provided by <a href="https://squiggle.com.au" target="_blank" rel="noopener">Squiggle</a>
      </p>
      <p class="text-muted">
        Open source project on <a href="https://github.com/danielpaparo98/WhatIsMyTip" target="_blank" rel="noopener">GitHub</a>
      </p>
      <a
        v-if="buyMeACoffeeUrl"
        :href="buyMeACoffeeUrl"
        target="_blank"
        rel="noopener"
        class="buy-me-coffee-btn"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 8h1a4 4 0 0 1 0 8h-1"></path>
          <path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"></path>
          <line x1="6" y1="1" x2="6" y2="4"></line>
          <line x1="10" y1="1" x2="10" y2="4"></line>
          <line x1="14" y1="1" x2="14" y2="4"></line>
        </svg>
        Buy me a coffee
      </a>
    </div>
  </footer>
</template>

<script setup lang="ts">
const config = useRuntimeConfig()
const buyMeACoffeeUrl = config.public.buyMeACoffeeUrl as string
</script>

<style scoped>
.footer {
  border-top: 1px solid var(--color-border);
  padding: 2rem 1.5rem;
  margin-top: 3rem;
}

.footer-content {
  max-width: 1400px;
  margin: 0 auto;
  text-align: center;
}

.footer-content p {
  margin: 0.5rem 0;
  font-size: 0.875rem;
}

.buy-me-coffee-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 1rem;
  padding: 0.625rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  color: #000000;
  background-color: #FFDD00;
  border: none;
  border-radius: 6px;
  text-decoration: none;
  transition: all 0.2s ease;
}

.buy-me-coffee-btn:hover {
  background-color: #F5CC00;
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.buy-me-coffee-btn svg {
  width: 16px;
  height: 16px;
}

@media (max-width: 640px) {
  .footer {
    padding: 1.5rem 1rem;
    margin-top: 2rem;
  }

  .footer-content p {
    font-size: 0.8125rem;
  }

  .buy-me-coffee-btn {
    font-size: 0.8125rem;
    padding: 0.5rem 1rem;
  }
}
</style>
