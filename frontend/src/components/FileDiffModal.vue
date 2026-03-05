<script setup lang="ts">
import { ref, watch } from "vue";
import { NButton, NIcon, NInput, NModal, NSpace, NSpin, useMessage } from "naive-ui";
import { PhEye } from "@phosphor-icons/vue";
import { fetchFilePreview } from "@/api/http";
import DiffViewer from "@/components/DiffViewer.vue";
import { useI18n } from "@/i18n";

const props = defineProps<{
  show: boolean;
  box: string | null;
  token: string | null;
  theme: "light" | "dark";
  initialFileA?: string;
  initialFileB?: string;
}>();

const emit = defineEmits<{
  (e: "update:show", value: boolean): void;
}>();

const { t } = useI18n();
const message = useMessage();

const fileA = ref("");
const fileB = ref("");
const contentA = ref("");
const contentB = ref("");
const loading = ref(false);
const compared = ref(false);
const languageA = ref("text");

const getLanguageFromFilename = (filename: string) => {
  const ext = filename.split(".").pop()?.toLowerCase();
  const langMap: Record<string, string> = {
    js: "javascript", jsx: "javascript", ts: "javascript", tsx: "javascript",
    py: "python", html: "html", htm: "html", css: "css", scss: "css", sass: "css",
    json: "json", md: "markdown", xml: "xml", svg: "xml",
  };
  return langMap[ext || ""] || "text";
};

watch(() => props.show, (showing) => {
  if (showing) {
    fileA.value = props.initialFileA || "";
    fileB.value = props.initialFileB || "";
    contentA.value = "";
    contentB.value = "";
    compared.value = false;
    if (fileA.value && fileB.value) {
      compare();
    }
  }
});

function isValidPath(p: string): boolean {
  return p.length > 0 && p.length <= 4096 && !p.includes("\0");
}

async function compare() {
  if (!props.box || !isValidPath(fileA.value.trim()) || !isValidPath(fileB.value.trim())) {
    message.warning("Please enter valid file paths");
    return;
  }
  loading.value = true;
  compared.value = false;
  try {
    const [payloadA, payloadB] = await Promise.all([
      fetchFilePreview(props.box, fileA.value.trim(), props.token),
      fetchFilePreview(props.box, fileB.value.trim(), props.token),
    ]);
    contentA.value = payloadA.content || "";
    contentB.value = payloadB.content || "";
    languageA.value = getLanguageFromFilename(fileA.value);
    compared.value = true;
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <NModal :show="show" preset="card" style="max-width: 95vw; max-height: 95vh" @update:show="emit('update:show', $event)">
    <template #header>
      <div class="modal-header">
        <NIcon size="16"><PhEye weight="duotone" /></NIcon>
        <span>Compare Files</span>
      </div>
    </template>

    <div class="diff-container">
      <NSpace size="small" align="center" class="diff-inputs">
        <NInput v-model:value="fileA" placeholder="File A path" size="small" style="flex: 1" @keyup.enter="compare" />
        <span class="text-muted">vs</span>
        <NInput v-model:value="fileB" placeholder="File B path" size="small" style="flex: 1" @keyup.enter="compare" />
        <NButton size="small" type="primary" :loading="loading" @click="compare" :disabled="!fileA.trim() || !fileB.trim()">Compare</NButton>
      </NSpace>

      <NSpin v-if="loading" size="large" style="margin-top: 24px">
        <span class="text-muted">Loading files...</span>
      </NSpin>

      <template v-else-if="compared">
        <div class="diff-labels">
          <span class="diff-label">{{ fileA.split('/').pop() }}</span>
          <span class="diff-label">{{ fileB.split('/').pop() }}</span>
        </div>
        <DiffViewer
          :original="contentA"
          :modified="contentB"
          :language="languageA"
          :theme="theme"
          style="height: 70vh"
        />
      </template>
    </div>

    <template #footer>
      <div class="modal-footer">
        <span v-if="compared && contentA === contentB" class="text-muted">Files are identical</span>
        <span v-else />
        <NButton @click="emit('update:show', false)">{{ t('common.close') }}</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.modal-header {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}

.modal-header > span {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.diff-container {
  min-height: 400px;
}

.diff-inputs {
  display: flex;
  width: 100%;
  margin-bottom: 12px;
}

.diff-labels {
  display: flex;
  gap: 8px;
  margin-bottom: 4px;
}

.diff-label {
  flex: 1;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--muted);
  padding: 4px 8px;
  background: var(--surface-variant);
  border-radius: 4px;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.text-muted {
  color: var(--muted);
}
</style>
