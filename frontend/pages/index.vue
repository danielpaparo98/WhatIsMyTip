<template>
  <div>
    <Header />
    <main class="main">
      <section class="hero">
        <h1>AI-Powered<br>Footy Tipping</h1>
        <p>Smart heuristics. Clear explanations. Better tips.</p>
      </section>

      <section class="section">
        <h2>Current Round Tips</h2>
        <div v-if="loading" class="loading">
          <div class="spinner"></div>
        </div>
        <div v-else-if="error" class="error">
          <p>{{ error }}</p>
          <button @click="loadTips" class="btn">Retry</button>
        </div>
        <div v-else-if="tips.length === 0" class="empty">
          <p>No tips available for the current round.</p>
          <button @click="generateTips" class="btn btn-primary">Generate Tips</button>
        </div>
        <div v-else class="tips-grid">
          <TipCard
            v-for="tip in tips"
            :key="tip.id"
            :heuristic="tip.heuristic"
            :selected-team="tip.selected_team"
            :margin="tip.margin"
            :confidence="tip.confidence"
            :explanation="tip.explanation"
          />
        </div>
      </section>
    </main>
    <Footer />
  </div>
</template>

<script setup lang="ts">
const api = useApi()

const loading = ref(true)
const error = ref<string | null>(null)
const tips = ref<any[]>([])

const loadTips = async () => {
  loading.value = true
  error.value = null
  
  try {
    const data = await api.getTips()
    tips.value = data.tips || []
  } catch (e) {
    error.value = 'Failed to load tips'
    console.error(e)
  } finally {
    loading.value = false
  }
}

const generateTips = async () => {
  try {
    const currentYear = new Date().getFullYear()
    await api.generateTips(currentYear, 1)
    await loadTips()
  } catch (e) {
    error.value = 'Failed to generate tips'
    console.error(e)
  }
}

onMounted(() => {
  loadTips()
})
</script>

<style scoped>
.main {
  max-width: 1400px;
  margin: 0 auto;
  padding: 2rem;
}

.hero {
  padding: 6rem 2rem;
  text-align: center;
}

.hero h1 {
  font-size: clamp(3rem, 10vw, 6rem);
  line-height: 0.95;
  margin-bottom: 1.5rem;
}

.hero p {
  font-size: 1.25rem;
  max-width: 600px;
  margin: 0 auto;
}

.section {
  padding: 4rem 2rem;
}

.section h2 {
  text-align: center;
  margin-bottom: 3rem;
}

.loading, .error, .empty {
  text-align: center;
  padding: 4rem 2rem;
}

.tips-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
}
</style>
