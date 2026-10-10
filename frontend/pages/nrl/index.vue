<template>
  <!-- NRL-TIPS (nrl-expansion-09): the first-class /nrl tips route.
       Nuxt serves this static page for /nrl (static segments outrank the
       pages/[league] dynamic param), so the shared league page stays
       byte-identical for the state leagues. The page contributes only its
       route key — all data wiring and presentation live in the view. -->
  <RugbyLeagueTipsView league="nrl" />
</template>

<script setup lang="ts">
// Thin by contract (pinned by rugby-league-tips-page.test.ts): no data
// fetching or presentation here — the shared view owns both.
import RugbyLeagueTipsView from '~/components/RugbyLeagueTipsView.vue'
</script>
