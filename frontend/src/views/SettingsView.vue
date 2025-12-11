<script setup lang="ts">
import { NCard, NDivider, NForm, NFormItem, NInput, NSelect, NSpace, NSwitch, NText } from "naive-ui";

import { useI18n } from "@/i18n";

const { locale, setLocale } = useI18n();
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <p class="eyebrow">settings</p>
        <h1>Bootstrap data + auth wiring</h1>
        <p class="text-muted">
          this screen will surface bootstrap metadata from the backend: token, allow-origins, locale, feature flags,
          and user prefs like theme or keyboard mode
        </p>
      </div>
    </header>

    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <span>planned fields</span>
      </div>
      <NForm label-placement="left" label-width="120">
        <NFormItem label="api base">
          <NInput placeholder="/api/v1" disabled />
        </NFormItem>
        <NFormItem label="token header">
          <NInput placeholder="X-SSHLER-TOKEN" disabled />
        </NFormItem>
        <NFormItem label="locale">
          <NSelect :options="[{ label: 'en', value: 'en' }, { label: 'ja', value: 'ja' }]" :value="locale" @update:value="(val) => setLocale(val as any)" />
        </NFormItem>
        <NFormItem label="theme">
          <NSpace size="small">
            <NSelect
              :options="[
                { label: 'system', value: 'system' },
                { label: 'light', value: 'light' },
                { label: 'dark', value: 'dark' },
              ]"
            />
            <NSwitch />
          </NSpace>
        </NFormItem>
      </NForm>
      <NDivider />
      <NText depth="3">wire this form to pinia + backend bootstrap payload when available</NText>
    </NCard>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-size: 12px;
  margin: 0 0 4px 0;
  color: var(--muted);
}

h1 {
  margin: 0 0 8px 0;
  font-size: 24px;
}

.surface-card {
  background: var(--surface);
  border-radius: 16px;
  border: 1px solid var(--stroke);
}

.card-title {
  font-weight: 600;
  margin-bottom: 8px;
}
</style>
