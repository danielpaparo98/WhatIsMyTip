<template>
  <footer class="footer">
    <div class="footer-content">
      <p class="text-muted">
        Data provided by <a href="https://squiggle.com.au" target="_blank" rel="noopener">Squiggle</a>
      </p>
      <p class="text-muted">
        Open source project on <a href="https://github.com" target="_blank" rel="noopener">GitHub</a>
      </p>
    </div>
  </footer>
</template>

<style scoped>
.footer {
  border-top: 1px solid var(--color-border);
  padding: 2rem;
  margin-top: 4rem;
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
</style>
