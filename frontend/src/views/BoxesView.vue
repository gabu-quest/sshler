<script setup lang="ts">
import { computed, onMounted } from "vue";

import { NAlert, NButton, NCard, NGrid, NGridItem, NIcon, NSpace, useMessage } from "naive-ui";
import { PhFolderSimple, PhPushPinSimple, PhStar, PhTerminalWindow } from "@phosphor-icons/vue";

import { useI18n } from "@/i18n";
import { boxStatus } from "@/api/http";
import type { ApiBox } from "@/api/types";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useFavoritesStore } from "@/stores/favorites";

const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const favoritesStore = useFavoritesStore();
const message = useMessage();
const { t } = useI18n();

const token = computed(() => bootstrapStore.token || bootstrapStore.payload?.token || null);

async function ensureBoxes() {
  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap();
  }
  if (!boxesStore.items.length && !boxesStore.loading) {
    await boxesStore.load(token.value);
  }
  if (boxesStore.items.length) {
    favoritesStore.hydrateFromBoxes(boxesStore.items);
  }
}

function patchBox(name: string, patch: Partial<ApiBox>) {
  boxesStore.setBoxes(
    boxesStore.items.map((box) => (box.name === name ? { ...box, ...patch } : box)),
  );
}

async function togglePin(boxName: string) {
  const pinned = await favoritesStore.setPinned(boxName, token.value);
  if (favoritesStore.error) {
    message.error(favoritesStore.error);
    return;
  }
  patchBox(boxName, { pinned });
  message.success(pinned ? t('boxes.pinned') : t('boxes.unpinned'));
}

async function toggleFavoritePath(boxName: string, path: string) {
  const desired = !favoritesStore.isFavorite(boxName, path);
  const nowFavorite = await favoritesStore.setFavorite(boxName, path, desired, token.value);
  if (favoritesStore.error) {
    message.error(favoritesStore.error);
    return;
  }
  const refreshed = await favoritesStore.loadBox(boxName, token.value);
  if (refreshed) {
    patchBox(boxName, {
      favorites: refreshed.favorites,
      pinned: refreshed.pinned,
    });
  } else {
    patchBox(boxName, { favorites: Array.from(favoritesStore.favoritesForBox(boxName).values()) });
  }
  message.success(nowFavorite ? t('boxes.favorited') : t('boxes.unfavorited'));
}

async function checkStatus(boxName: string) {
  const stat = await boxStatus(boxName, token.value);
  message.info(t('boxes.status_info', { status: stat.status, latency: String(stat.latency_ms || 0) }));
}

onMounted(async () => {
  await ensureBoxes();
});
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <p class="eyebrow">{{ t('boxes.title') }}</p>
        <h1>{{ t('boxes.subtitle') }}</h1>
        <p class="text-muted">{{ t('boxes.description') }}</p>
      </div>
    </header>

    <NGrid :x-gap="12" :y-gap="12" :cols="3">
      <NGridItem v-for="box in boxesStore.items" :key="box.name">
        <NCard class="surface-card" size="small">
          <div class="card-title">
            <NIcon size="16">
              <PhFolderSimple weight="duotone" />
            </NIcon>
            <span>{{ box.name }}</span>
          </div>
          <p class="text-muted small">{{ box.host }} • {{ box.transport }}</p>
          <NSpace size="small" style="margin-bottom: 8px;">
            <NButton size="tiny" type="primary" @click="() => $router.push(`/files?box=${box.name}`)">
              <NIcon size="14"><PhFolderSimple weight="duotone" /></NIcon>
              {{ t('boxes.files') }}
            </NButton>
            <NButton size="tiny" type="primary" @click="() => $router.push(`/terminal?box=${box.name}`)">
              <NIcon size="14"><PhTerminalWindow weight="duotone" /></NIcon>
              {{ t('boxes.terminal') }}
            </NButton>
            <NButton size="tiny" @click="() => $router.push(`/multi-terminal?box=${box.name}`)">
              {{ t('boxes.multi') }}
            </NButton>
          </NSpace>
          <NSpace size="small">
            <NButton size="tiny" secondary @click="() => togglePin(box.name)">
              <NIcon size="14"><PhPushPinSimple weight="duotone" /></NIcon>
              {{ favoritesStore.isPinned(box.name) ? t('boxes.unpin') : t('boxes.pin') }}
            </NButton>
            <NButton size="tiny" secondary @click="() => toggleFavoritePath(box.name, box.default_dir || '/')">
              <NIcon size="14"><PhStar weight="duotone" /></NIcon>
              {{ favoritesStore.isFavorite(box.name, box.default_dir || '/') ? t('boxes.unfavorite') : t('boxes.favorite') }}
            </NButton>
            <NButton size="tiny" quaternary @click="() => checkStatus(box.name)">{{ t('common.status') }}</NButton>
          </NSpace>
        </NCard>
      </NGridItem>
    </NGrid>

    <NAlert v-if="boxesStore.error" type="error" closable>{{ boxesStore.error }}</NAlert>
    <NAlert v-else-if="favoritesStore.error" type="error" closable>{{ favoritesStore.error }}</NAlert>
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
  gap: 4px;
}
.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-size: 12px;
  margin: 0;
  color: var(--muted);
}
.small {
  font-size: 12px;
}
</style>
