<template>
  <div>
    <a href="#main-content" class="skip-link">Skip to main content</a>
    <Header />
    <main id="main-content" class="main">
      <slot />
    </main>
    <Footer />
  </div>
</template>

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
