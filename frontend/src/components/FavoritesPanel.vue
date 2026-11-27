<script setup lang="ts">
import { computed } from "vue";

import { NAlert, NButton, NCard, NIcon, NList, NListItem } from "naive-ui";
import { PhStar, PhPushPinSimple } from "@phosphor-icons/vue";

import { useFavoritesStore } from "@/stores/favorites";
import { useI18n } from "@/i18n";
import { pathSegments } from "@/utils/pathParts";

const props = defineProps<{ box: string | null; compact?: boolean }>();
const emit = defineEmits<{ (e: "openPath", path: string): void; (e: "togglePin"): void }>();

const favoritesStore = useFavoritesStore();
const { t } = useI18n();

const favoritesList = computed(() =>
  props.box ? Array.from(favoritesStore.favoritesForBox(props.box).values()) : [],
);
const pinned = computed(() => favoritesStore.isPinned(props.box));

// A chip shows the last folder of the path; when two chips would read the same, it shows the
// parent folder too (`api/src`, `web/src`). Its title and its click carry the whole path.
const lastTwo = (path: string): string[] => pathSegments(path).map((s) => s.label).filter(Boolean)
const chipLabel = (path: string): string => {
  const names = lastTwo(path)
  const last = names[names.length - 1]
  if (!last) return path
  const clash = favoritesList.value.some((other) => {
    if (other === path) return false
    const o = lastTwo(other)
    return o[o.length - 1] === last
  })
  return clash && names.length > 1 ? `${names[names.length - 2]}/${last}` : last
}
</script>

<template>
  <div v-if="compact" class="fav-strip" data-testid="favorites-strip">
    <NIcon size="14" class="fav-icon"><PhStar weight="duotone" /></NIcon>
    <div class="fav-chips">
      <span v-if="!box" class="fav-hint">{{ t('favorites.select_box') }}</span>
      <span v-else-if="!favoritesList.length" class="fav-hint">{{ t('favorites.empty') }}</span>
      <button
        v-for="item in favoritesList"
        v-else
        :key="item"
        type="button"
        class="fav-chip"
        :title="item"
        @click="emit('openPath', item)"
      >{{ chipLabel(item) }}</button>
    </div>
    <NButton class="fav-pin" size="tiny" quaternary :disabled="!box" :title="pinned ? t('favorites.unpin') : t('favorites.pin')" @click="emit('togglePin')">
      <NIcon size="14"><PhPushPinSimple :weight="pinned ? 'fill' : 'duotone'" /></NIcon>
    </NButton>
    <NAlert v-if="favoritesStore.error" type="error" closable>{{ favoritesStore.error }}</NAlert>
  </div>
  <NCard v-else class="surface-card" size="small">
    <div class="card-title">
      <NIcon size="16">
        <PhStar weight="duotone" />
      </NIcon>
      <span>{{ t('favorites.title') }}</span>
    </div>
    <NAlert v-if="!box" type="info" closable>{{ t('favorites.select_box') }}</NAlert>
    <NAlert v-else-if="!favoritesList.length" type="default" closable>{{ t('favorites.empty') }}</NAlert>
    <NList v-else class="list">
      <NListItem
        v-for="item in favoritesList"
        :key="item"
        class="fav-item"
        @click="emit('openPath', item)"
      >
        {{ item }}
      </NListItem>
    </NList>
    <div class="pin-row">
      <NButton size="tiny" secondary :disabled="!box" @click="emit('togglePin')">
        <NIcon size="14">
          <PhPushPinSimple weight="duotone" />
        </NIcon>
        <span>{{ pinned ? t('favorites.unpin') : t('favorites.pin') }}</span>
      </NButton>
    </div>
    <NAlert v-if="favoritesStore.error" type="error" closable>{{ favoritesStore.error }}</NAlert>
  </NCard>
</template>

<style scoped>
.list {
  max-height: 180px;
  overflow: auto;
}

.fav-item {
  cursor: pointer;
  color: var(--text);
}

.fav-item:hover {
  background: var(--surface-hover);
}

.pin-row {
  margin-top: 8px;
}

.fav-strip {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.fav-icon {
  flex: 0 0 auto;
  color: var(--muted);
}

.fav-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.fav-chips::-webkit-scrollbar {
  display: none;
}

.fav-hint {
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}

.fav-chip {
  flex: 0 0 auto;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 2px 10px;
  border: 1px solid var(--stroke);
  border-radius: 999px;
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.fav-chip:hover {
  background: var(--surface-hover);
  border-color: var(--stroke-hover);
  color: var(--text);
}

.fav-chip:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
</style>
