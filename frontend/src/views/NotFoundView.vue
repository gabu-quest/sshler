<script setup lang="ts">
import { NButton, NCard } from "naive-ui";
</script>

<template>
  <div class="page center">
    <NCard class="surface-card" size="medium">
      <h2>Not found</h2>
      <p class="text-muted">this route is not mapped yet; return home</p>
      <NButton type="primary" text tag="a" href="/app/">back to overview</NButton>
    </NCard>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex: 1;
  justify-content: center;
  align-items: center;
}

.surface-card {
  background: var(--surface);
  border-radius: 16px;
  border: 1px solid var(--stroke);
  min-width: 280px;
  text-align: center;
}

h2 {
  margin-top: 0;
}
</style>
