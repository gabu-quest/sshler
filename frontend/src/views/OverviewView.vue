<script setup lang="ts">
import { computed, onMounted } from "vue";

import { NAlert, NButton, NCard, NGrid, NGridItem, NIcon, NSpin, NTag } from "naive-ui";
import {
  PhCloudArrowDown,
  PhCode,
  PhRocketLaunch,
  PhShieldCheck,
} from "@phosphor-icons/vue";

import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useI18n } from "@/i18n";

const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const { t } = useI18n();

const boxCount = computed(() => boxesStore.items.length);
const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token);

onMounted(async () => {
  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap();
  }
  if (!boxesStore.items.length && !boxesStore.loading) {
    await boxesStore.load(tokenValue.value || null);
  }
});
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <p class="eyebrow">vue spa rollout</p>
        <h1>{{ t("overview_heading") }}</h1>
        <p class="text-muted">
          this is the scaffold for the new ui; we ship a bundled dist from fastapi at /app while the legacy
          htmx screens stay intact until parity
        </p>
      </div>
      <div class="chips">
        <NTag type="info" size="small" round>vite</NTag>
        <NTag type="success" size="small" round>naive ui</NTag>
        <NTag type="success" size="small" round>pinia</NTag>
        <NTag type="warning" size="small" round>router / composition api</NTag>
      </div>
    </header>

    <NGrid :x-gap="16" :y-gap="16" :cols="2">
      <NGridItem>
        <NCard class="surface-card" size="medium">
          <div class="card-title">
            <NIcon size="18">
              <PhRocketLaunch />
            </NIcon>
            <span>what works now</span>
          </div>
          <ul class="bullets">
            <li>vite pipeline set to build into sshler/static/dist with base /app</li>
            <li>fastapi can mount the dist once built so we ship a zero-node runtime</li>
            <li>router + pinia scaffold ready for boxes, files, terminal, settings views</li>
            <li>theme toggles light/dark and keeps system preference</li>
          </ul>
        </NCard>
      </NGridItem>
      <NGridItem>
        <NCard class="surface-card" size="medium">
          <div class="card-title">
            <NIcon size="18">
              <PhCode />
            </NIcon>
            <span>immediate next steps</span>
          </div>
          <ul class="bullets">
            <li>add /api/v1 endpoints and thin client wrappers for boxes, files, sessions, websocket handshake</li>
            <li>drop in xterm integration and wire resize throttling parity with the legacy ui</li>
            <li>port bookmarks/recent files, upload progress, and command palette into stores + views</li>
            <li>add vitest + playwright hooks in pnpm scripts and ci</li>
          </ul>
        </NCard>
      </NGridItem>
    </NGrid>

    <NGrid :x-gap="16" :y-gap="16" :cols="3" class="pill-grid">
      <NGridItem>
        <NCard class="surface-card compact" size="small">
          <div class="pill">
            <NIcon size="16">
              <PhCloudArrowDown />
            </NIcon>
            <div>
              <p class="pill-title">bundled dist</p>
              <p class="text-muted small">pnpm build writes into python package; served at /app</p>
            </div>
          </div>
        </NCard>
      </NGridItem>
      <NGridItem>
        <NCard class="surface-card compact" size="small">
          <div class="pill">
            <NIcon size="16">
              <PhShieldCheck />
            </NIcon>
            <div>
              <p class="pill-title">auth model</p>
              <p class="text-muted small">honor x-sshler-token and basic auth on all api calls</p>
            </div>
          </div>
        </NCard>
      </NGridItem>
      <NGridItem>
        <NCard class="surface-card compact" size="small">
          <div class="pill">
            <NIcon size="16">
              <RocketLaunch />
            </NIcon>
            <div>
              <p class="pill-title">pwa + mobile</p>
              <p class="text-muted small">reuse existing sw/manifest; keep keyboard + resize optimizations</p>
            </div>
          </div>
        </NCard>
      </NGridItem>
    </NGrid>

    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <NIcon size="18">
          <PhCode />
        </NIcon>
        <span>live bootstrap</span>
      </div>
      <div class="bootstrap">
        <div>
          <p class="label">version</p>
          <p class="value">{{ bootstrapStore.version || "unknown" }}</p>
        </div>
        <div>
          <p class="label">token</p>
          <p class="value monospace">
            <NSpin v-if="bootstrapStore.loading" size="small" /> <span v-else>{{ tokenValue || "not set" }}</span>
          </p>
        </div>
        <div>
          <p class="label">boxes</p>
          <p class="value">{{ boxesStore.loading ? "loading..." : boxCount }}</p>
        </div>
      </div>
      <NAlert v-if="bootstrapStore.error || boxesStore.error" type="error" closable class="mt">
        {{ bootstrapStore.error || boxesStore.error }}
      </NAlert>
    </NCard>

    <footer class="page-footer">
      <NButton type="primary" secondary tag="a" href="/docs" target="_blank">view legacy docs</NButton>
      <NButton tag="a" href="https://vite.dev/guide/" target="_blank">vite docs</NButton>
    </footer>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 4px;
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
  font-size: 28px;
  letter-spacing: 0.2px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.surface-card {
  background: var(--surface);
  border-radius: 16px;
  border: 1px solid var(--stroke);
}

.card-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  margin-bottom: 8px;
}

.bullets {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: var(--muted);
}

.pill-grid {
  margin-top: 4px;
}

.pill {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  align-items: center;
}

.pill-title {
  margin: 0 0 4px 0;
  font-weight: 600;
}

.small {
  font-size: 13px;
}

.page-footer {
  display: flex;
  gap: 10px;
  padding: 4px 0 12px;
}

.bootstrap {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}

.label {
  text-transform: uppercase;
  letter-spacing: 0.3px;
  font-size: 12px;
  margin: 0 0 4px 0;
  color: var(--muted);
}

.value {
  margin: 0;
  font-weight: 600;
}

.monospace {
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
}

.mt {
  margin-top: 12px;
}

@media (max-width: 960px) {
  .page-header {
    flex-direction: column;
  }
}
</style>
