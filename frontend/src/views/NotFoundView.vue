<script setup lang="ts">
import { NButton, NCard } from "naive-ui";

import { useI18n } from "@/i18n";

const { t } = useI18n();
</script>

<template>
  <div class="page center">
    <NCard class="surface-card" size="medium">
      <h2>{{ t("notfound.title") }}</h2>
      <p class="text-muted">{{ t("notfound.body") }}</p>
      <NButton type="primary" text tag="a" href="/app/">{{ t("notfound.back") }}</NButton>
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
  border-radius: var(--radius-xl);
  border: 1px solid var(--stroke);
  min-width: 280px;
  text-align: center;
}

h2 {
  margin-top: 0;
}
</style>
