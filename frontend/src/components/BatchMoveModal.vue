<script setup lang="ts">
import { ref } from "vue";
import { NModal, NCard, NButton, NSpace, NIcon } from "naive-ui";
import { PhArrowBendUpLeft, PhCopy } from "@phosphor-icons/vue";
import DirectorySearchInput from "@/components/DirectorySearchInput.vue";
import { useI18n } from "@/i18n";

const props = defineProps<{
  visible: boolean;
  mode: "move" | "copy";
  box: string | null;
  token: string | null;
  count: number;
}>();

const emit = defineEmits<{
  (e: "confirm", destination: string): void;
  (e: "close"): void;
}>();

const { t } = useI18n();
const destination = ref("");

function handleSelect(path: string) {
  destination.value = path;
}

function handleConfirm() {
  if (!destination.value) return;
  emit("confirm", destination.value);
}
</script>

<template>
  <NModal :show="visible" @update:show="(v: boolean) => { if (!v) emit('close') }">
    <NCard
      :title="t(mode === 'move' ? 'files.batch_title_move' : 'files.batch_title_copy', { count })"
      style="max-width: 480px; width: 100%"
      closable
      @close="emit('close')"
    >
      <NSpace vertical size="medium">
        <div style="font-size: 13px; color: var(--muted)">
          {{ t('files.batch_search_hint') }}
        </div>
        <DirectorySearchInput
          :box="box"
          :token="token"
          @select="handleSelect"
        />
        <div v-if="destination" style="font-size: 13px; font-family: var(--font-mono); padding: 8px 12px; background: var(--surface-variant); border-radius: 6px; word-break: break-all">
          {{ destination }}
        </div>
      </NSpace>
      <template #action>
        <NSpace justify="end">
          <NButton @click="emit('close')">{{ t('common.cancel') }}</NButton>
          <NButton
            :type="mode === 'move' ? 'warning' : 'primary'"
            :disabled="!destination"
            @click="handleConfirm"
          >
            <NIcon size="14" style="margin-right: 4px">
              <PhArrowBendUpLeft v-if="mode === 'move'" weight="duotone" />
              <PhCopy v-else weight="duotone" />
            </NIcon>
            {{ t(mode === 'move' ? 'files.batch_move_here' : 'files.batch_copy_here') }}
          </NButton>
        </NSpace>
      </template>
    </NCard>
  </NModal>
</template>
