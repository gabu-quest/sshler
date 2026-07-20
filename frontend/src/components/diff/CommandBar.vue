<script setup lang="ts">
import { computed, ref } from "vue";
import { NInput, NButton, NIcon, NTag } from "naive-ui";
import { PhTerminalWindow, PhQuestion, PhX } from "@phosphor-icons/vue";

import { parseCommand } from "@/utils/diffCommandParser";
import type { Command, ParseError } from "@/utils/diffCommandParser";
import { useI18n } from "@/i18n";

const props = defineProps<{
  defaultRepoLabel?: string | null;
}>();

const emit = defineEmits<{
  (e: "exec", command: Command): void;
  (e: "help"): void;
}>();

const { t } = useI18n();
const inputEl = ref<InstanceType<typeof NInput> | null>(null);
const buffer = ref("");
const error = ref<string | null>(null);

function focus() {
  inputEl.value?.focus?.();
}
defineExpose({ focus });

function submit() {
  const result = parseCommand(buffer.value);
  if (result.type === "error") {
    error.value = (result as ParseError).message;
    return;
  }
  if (result.type === "help") {
    emit("help");
    error.value = null;
    buffer.value = "";
    return;
  }
  emit("exec", result);
  error.value = null;
  buffer.value = "";
}

function clearBuffer() {
  buffer.value = "";
  error.value = null;
}

const placeholder = computed(() => t("diff.command.placeholder"));
</script>

<template>
  <div class="command-bar" data-testid="diff-command-bar">
    <div class="bar-row">
      <NIcon size="14" class="prefix" aria-hidden="true">
        <PhTerminalWindow weight="duotone" />
      </NIcon>
      <NInput
        ref="inputEl"
        v-model:value="buffer"
        :placeholder="placeholder"
        size="small"
        :aria-label="t('diff.command.placeholder')"
        @keyup.enter="submit"
        @keyup.escape="clearBuffer"
        data-testid="diff-command-input"
      />
      <NTag
        v-if="props.defaultRepoLabel"
        size="small"
        type="info"
        :title="t('diff.command.default_repo_tooltip')"
      >
        {{ props.defaultRepoLabel }}
      </NTag>
      <NButton
        quaternary
        circle
        size="small"
        :title="t('diff.command.clear')"
        :aria-label="t('diff.command.clear')"
        :disabled="!buffer && !error"
        @click="clearBuffer"
      >
        <NIcon size="14"><PhX weight="bold" /></NIcon>
      </NButton>
      <NButton
        quaternary
        circle
        size="small"
        :title="t('diff.command.help')"
        :aria-label="t('diff.command.help')"
        @click="emit('help')"
        data-testid="diff-command-help"
      >
        <NIcon size="16"><PhQuestion weight="duotone" /></NIcon>
      </NButton>
    </div>
    <div v-if="error" class="bar-error" role="alert" data-testid="diff-command-error">
      {{ error }}
    </div>
  </div>
</template>

<style scoped>
.command-bar {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 10px;
  border: 1px solid var(--stroke);
  border-radius: 10px;
  background: var(--panel-bg);
}

.bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.prefix {
  color: var(--muted);
  flex-shrink: 0;
}

.bar-error {
  font-size: 12px;
  color: var(--danger-color, #ef4444);
  padding-left: 24px;
}
</style>
