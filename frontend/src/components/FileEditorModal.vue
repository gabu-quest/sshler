<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NButton, NIcon, NModal, NSpace, NSpin, NSwitch, useDialog, useMessage } from "naive-ui";
import { PhPencil, PhUploadSimple } from "@phosphor-icons/vue";
import { fetchFilePreview, writeFile } from "@/api/http";
import CodeEditor from "@/components/CodeEditor.vue";
import { useI18n } from "@/i18n";
import { getLanguageForFilename } from "@/utils/codeLanguage";

const props = defineProps<{
  show: boolean;
  path: string;
  box: string | null;
  token: string | null;
  theme: "light" | "dark";
}>();

const emit = defineEmits<{
  (e: "update:show", value: boolean): void;
  (e: "saved", path: string, content: string): void;
}>();

const { t } = useI18n();
const message = useMessage();
const dialog = useDialog();

const LINE_NUMBERS_KEY = "sshler:editor:lineNumbers";
const WORD_WRAP_KEY = "sshler:editor:wordWrap";

function readPersistedBoolean(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    return raw === "true";
  } catch {
    return fallback;
  }
}

function writePersistedBoolean(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    // localStorage unavailable (private mode, tests, etc.) — ignore
  }
}

const content = ref("");
const originalContent = ref("");
const loading = ref(false);
const showLineNumbers = ref(readPersistedBoolean(LINE_NUMBERS_KEY, true));
const wordWrap = ref(readPersistedBoolean(WORD_WRAP_KEY, true));

const isDirty = computed(() => content.value !== originalContent.value);

watch(showLineNumbers, (value) => writePersistedBoolean(LINE_NUMBERS_KEY, value));
watch(wordWrap, (value) => writePersistedBoolean(WORD_WRAP_KEY, value));

watch(() => props.show, async (showing) => {
  if (!showing || !props.box || !props.path) return;
  content.value = "";
  originalContent.value = "";
  loading.value = true;
  try {
    const payload = await fetchFilePreview(props.box, props.path, props.token);
    content.value = payload.content || "";
    originalContent.value = content.value;
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
    emit("update:show", false);
  } finally {
    loading.value = false;
  }
});

function closeModal() {
  emit("update:show", false);
}

function requestClose() {
  if (!isDirty.value) {
    closeModal();
    return;
  }
  dialog.warning({
    title: t("editor.discard_title"),
    content: t("editor.discard_confirm"),
    positiveText: t("editor.discard_action"),
    negativeText: t("editor.keep_editing"),
    positiveButtonProps: { type: "error" } as any,
    onPositiveClick: () => closeModal(),
  });
}

async function saveEdit() {
  if (!props.box || !props.path) return;
  loading.value = true;
  try {
    await writeFile(props.box, props.path, content.value, props.token);
    originalContent.value = content.value;
    message.success(t("files.saved"));
    emit("saved", props.path, content.value);
    closeModal();
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <NModal :show="show" preset="card" style="max-width: 95vw; max-height: 95vh" @update:show="(value) => { if (!value) requestClose(); }">
    <template #header>
      <div class="modal-header">
        <NIcon size="16"><PhPencil weight="duotone" /></NIcon>
        <span>{{ t('common.edit') }}: {{ path.split('/').pop() }}</span>
      </div>
    </template>

    <div class="editor-container">
      <NSpin v-if="loading" size="large"><span class="text-muted">{{ t('files.loading_file') }}</span></NSpin>
      <CodeEditor v-else v-model:model-value="content" :language="getLanguageForFilename(path)" :theme="theme" :line-numbers="showLineNumbers" :word-wrap="wordWrap" style="height: 80vh" :placeholder="t('files.file_placeholder')" @save="saveEdit" />
    </div>

    <template #footer>
      <div class="modal-footer">
        <NSpace size="small" :wrap="false">
          <NSwitch v-model:value="showLineNumbers" size="small">
            <template #checked>{{ t('files.lines') }}</template>
            <template #unchecked>{{ t('files.no_lines') }}</template>
          </NSwitch>
          <NSwitch v-model:value="wordWrap" size="small">
            <template #checked>{{ t('files.wrap') }}</template>
            <template #unchecked>{{ t('files.no_wrap') }}</template>
          </NSwitch>
        </NSpace>
        <NSpace size="small" :wrap="false">
          <NButton @click="requestClose">{{ t('common.cancel') }}</NButton>
          <NButton type="primary" :loading="loading" @click="saveEdit">
            <NIcon size="14"><PhUploadSimple weight="duotone" /></NIcon>{{ t('common.save') }}
          </NButton>
        </NSpace>
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

.editor-container {
  min-height: 400px;
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
