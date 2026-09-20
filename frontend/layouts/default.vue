<template>
  <div>
    <a href="#main-content" class="skip-link">Skip to main content</a>
    <!-- GF-DESIGN (2026-09-20, user request): ConfettiEffect moved to
         index.vue — confetti fires on the HOME PAGE ONLY. -->
    <OffSeasonBanner />
    <Header />
    <main id="main-content" class="main">
      <slot />
    </main>
    <Footer />
  </div>
</template>

<script setup lang="ts">
// Site-wide: the off-season banner is self-contained (reads the shared
// latest-round store).  Confetti lives on the home page only.
</script>

<style scoped>
.skip-link {
  position: absolute;
  top: -40px;
  left: 0;
  background: #000;
  color: #fff;
  padding: 8px;
  z-index: 100;
  transition: top 0.2s;
}

.skip-link:focus {
  top: 0;
}
</style>
