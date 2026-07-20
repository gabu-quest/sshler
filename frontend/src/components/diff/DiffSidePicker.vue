<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NSelect, NInput, NAutoComplete, NIcon } from "naive-ui";
import { PhArrowLeft, PhArrowRight, PhGitBranch, PhFolder, PhFile } from "@phosphor-icons/vue";

import { useBoxesStore } from "@/stores/boxes";
import { useBootstrapStore } from "@/stores/bootstrap";
import { gitBranches } from "@/api/http";
import type { DiffSide } from "@/stores/diff";
import { useI18n } from "@/i18n";

const props = defineProps<{
  side: DiffSide;
  variant: "left" | "right";
}>();

const emit = defineEmits<{
  (e: "update:side", side: DiffSide): void;
}>();

const { t } = useI18n();
const boxesStore = useBoxesStore();
const bootstrapStore = useBootstrapStore();

const local = ref<DiffSide>({ ...props.side });

watch(() => props.side, (s) => {
  // Only overwrite local state when the parent pushes a structurally-different value.
  // Avoids fighting with the user typing into NInput.
  if (
    s.box !== local.value.box ||
    s.directory !== local.value.directory ||
    s.ref !== local.value.ref ||
    s.path !== local.value.path
  ) {
    local.value = { ...s };
  }
}, { deep: true });

function emitChange() {
  emit("update:side", { ...local.value });
}

const boxOptions = computed(() =>
  boxesStore.items.map((b) => ({ label: b.name, value: b.name })),
);

const refOptions = ref<{ label: string; value: string }[]>([]);
const loadingRefs = ref(false);

async function loadRefs() {
  if (!local.value.box) {
    refOptions.value = [];
    return;
  }
  loadingRefs.value = true;
  try {
    const res = await gitBranches(
      local.value.box,
      local.value.directory || "/",
      bootstrapStore.token,
    );
    refOptions.value = (res.branches ?? []).map((b) => ({
      label: b.name,
      value: b.name,
    }));
  } catch {
    refOptions.value = [];
  } finally {
    loadingRefs.value = false;
  }
}

watch(() => [local.value.box, local.value.directory], () => {
  loadRefs();
});

// Initial load
loadRefs();

const variantIcon = computed(() => (props.variant === "left" ? PhArrowLeft : PhArrowRight));
const variantLabel = computed(() =>
  props.variant === "left" ? t("diff.side.left") : t("diff.side.right"),
);
</script>

<template>
  <div class="side-picker" :data-testid="`diff-side-${variant}`">
    <header class="side-header">
      <NIcon size="14" :color="variant === 'left' ? '#f97316' : '#22c55e'">
        <component :is="variantIcon" weight="duotone" />
      </NIcon>
      <span class="side-label">{{ variantLabel }}</span>
    </header>

    <label class="field">
      <span class="field-label">{{ t("diff.field.box") }}</span>
      <NSelect
        v-model:value="local.box"
        :options="boxOptions"
        :placeholder="t('diff.placeholder.box')"
        filterable
        clearable
        size="small"
        @update:value="emitChange"
        :data-testid="`diff-box-${variant}`"
      />
    </label>

    <label class="field">
      <span class="field-label">
        <NIcon size="12"><PhFolder weight="duotone" /></NIcon>
        {{ t("diff.field.directory") }}
      </span>
      <NInput
        v-model:value="local.directory"
        :placeholder="t('diff.placeholder.directory')"
        size="small"
        @blur="emitChange"
        @keyup.enter="emitChange"
        :data-testid="`diff-directory-${variant}`"
      />
    </label>

    <label class="field">
      <span class="field-label">
        <NIcon size="12"><PhGitBranch weight="duotone" /></NIcon>
        {{ t("diff.field.ref") }}
      </span>
      <NAutoComplete
        v-model:value="local.ref"
        :options="refOptions"
        :placeholder="t('diff.placeholder.ref')"
        :loading="loadingRefs"
        size="small"
        :clear-after-select="false"
        @select="emitChange"
        @blur="emitChange"
        @keyup.enter="emitChange"
        :input-props="{ 'data-testid': `diff-ref-${variant}` }"
      />
    </label>

    <label class="field">
      <span class="field-label">
        <NIcon size="12"><PhFile weight="duotone" /></NIcon>
        {{ t("diff.field.path") }}
      </span>
      <NInput
        v-model:value="local.path"
        :placeholder="t('diff.placeholder.path')"
        size="small"
        @blur="emitChange"
        @keyup.enter="emitChange"
        :data-testid="`diff-path-${variant}`"
      />
    </label>
  </div>
</template>

<style scoped>
.side-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--stroke);
  border-radius: 10px;
  background: var(--panel-bg);
  min-width: 0;
}

.side-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: var(--muted);
  margin-bottom: 2px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--muted);
}
</style>
