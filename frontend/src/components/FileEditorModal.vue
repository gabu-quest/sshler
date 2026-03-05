<script setup lang="ts">
import { ref, watch } from "vue";
import { NButton, NIcon, NModal, NSpace, NSpin, NSwitch, useMessage } from "naive-ui";
import { PhPencil, PhUploadSimple } from "@phosphor-icons/vue";
import { fetchFilePreview, writeFile } from "@/api/http";
import CodeEditor from "@/components/CodeEditor.vue";
import { useI18n } from "@/i18n";

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

const content = ref("");
const loading = ref(false);
const showLineNumbers = ref(true);
const wordWrap = ref(true);

const getLanguageFromFilename = (filename: string) => {
  const ext = filename.split(".").pop()?.toLowerCase();
  const langMap: Record<string, string> = {
    js: "javascript", jsx: "javascript", ts: "javascript", tsx: "javascript",
    py: "python", html: "html", htm: "html", css: "css", scss: "css", sass: "css",
    json: "json", md: "markdown", xml: "xml", svg: "xml",
  };
  return langMap[ext || ""] || "text";
};

watch(() => props.show, async (showing) => {
  if (!showing || !props.box || !props.path) return;
  content.value = "";
  loading.value = true;
  try {
    const payload = await fetchFilePreview(props.box, props.path, props.token);
    content.value = payload.content || "";
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
    emit("update:show", false);
  } finally {
    loading.value = false;
  }
});

async function saveEdit() {
  if (!props.box || !props.path) return;
  loading.value = true;
  try {
    await writeFile(props.box, props.path, content.value, props.token);
    message.success(t("files.saved"));
    emit("saved", props.path, content.value);
    emit("update:show", false);
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
        <NIcon size="16"><PhPencil weight="duotone" /></NIcon>
        <span>{{ t('common.edit') }}: {{ path.split('/').pop() }}</span>
      </div>
    </template>

    <div class="editor-container">
      <NSpin v-if="loading" size="large"><span class="text-muted">{{ t('files.loading_file') }}</span></NSpin>
      <CodeEditor v-else v-model:model-value="content" :language="getLanguageFromFilename(path)" :theme="theme" :line-numbers="showLineNumbers" :word-wrap="wordWrap" style="height: 80vh" :placeholder="t('files.file_placeholder')" @save="saveEdit" />
    </div>

    <template #footer>
      <div class="modal-footer">
        <NSpace size="small" :wrap="false">
          <NSwitch v-model:value="showLineNumbers" size="small">
            <template #checked>{{ t('files.lines') }}</template>
            <template #unchecked>No Lines</template>
          </NSwitch>
          <NSwitch v-model:value="wordWrap" size="small">
            <template #checked>{{ t('files.wrap') }}</template>
            <template #unchecked>No Wrap</template>
          </NSwitch>
        </NSpace>
        <NSpace size="small" :wrap="false">
          <NButton @click="emit('update:show', false)">{{ t('common.cancel') }}</NButton>
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
