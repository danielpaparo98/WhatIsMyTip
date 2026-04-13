<template>
  <div class="error-page">
    <div class="error-content">
      <h1>{{ error?.statusCode || 'Error' }}</h1>
      <p>{{ error?.statusMessage || 'Something went wrong' }}</p>
      <NuxtLink to="/" class="back-home">← Back to Home</NuxtLink>
    </div>
  </div>
</template>

<script setup lang="ts">
interface Props {
  error?: {
    statusCode?: number
    statusMessage?: string
  }
}

const props = defineProps<Props>()
</script>

<style scoped>
.error-page {
  min-height: 60vh;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
}

.error-content h1 {
  font-size: 4rem;
  margin-bottom: 1rem;
}

.error-content p {
  color: var(--color-muted);
  font-size: 1.125rem;
}

.back-home {
  display: inline-block;
  margin-top: 2rem;
  padding: 0.75rem 1.5rem;
  background: #000;
  color: #fff;
  text-decoration: none;
  border-radius: 4px;
}

.back-home:hover {
  background: #333;
}
</style>
