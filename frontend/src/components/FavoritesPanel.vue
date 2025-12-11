<script setup lang="ts">
import { computed } from "vue";

import { NAlert, NButton, NCard, NIcon, NList, NListItem } from "naive-ui";
import { PhStar, PhPushPinSimple } from "@phosphor-icons/vue";

import { useFavoritesStore } from "@/stores/favorites";

const props = defineProps<{ box: string | null }>();
const emit = defineEmits<{ (e: "openPath", path: string): void; (e: "togglePin"): void }>();

const favoritesStore = useFavoritesStore();

const favoritesList = computed(() =>
  props.box ? Array.from(favoritesStore.favoritesForBox(props.box).values()) : [],
);
const pinned = computed(() => favoritesStore.isPinned(props.box));
</script>

<template>
  <NCard class="surface-card" size="small">
    <div class="card-title">
      <NIcon size="16">
        <PhStar />
      </NIcon>
      <span>favorites</span>
    </div>
    <NAlert v-if="!box" type="info" closable>select a box to view favorites</NAlert>
    <NAlert v-else-if="!favoritesList.length" type="default" closable>no favorites yet</NAlert>
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
          <PhPushPinSimple />
        </NIcon>
        <span>{{ pinned ? "unpin" : "pin" }}</span>
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
  background: rgba(255, 255, 255, 0.04);
}

.pin-row {
  margin-top: 8px;
}
</style>
