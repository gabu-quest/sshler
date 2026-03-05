<script setup lang="ts">
import { ref } from "vue";
import { NIcon } from "naive-ui";
import { PhUploadSimple } from "@phosphor-icons/vue";
import { useI18n } from "@/i18n";

defineProps<{
  currentDir: string;
}>();

const emit = defineEmits<{
  (e: "upload", files: FileList): void;
}>();

const { t } = useI18n();

const dragCounter = ref(0);
const isDragOver = ref(false);

const handleDragEnter = (e: DragEvent) => {
  e.preventDefault();
  dragCounter.value++;
  if (dragCounter.value === 1) isDragOver.value = true;
};

const handleDragLeave = (e: DragEvent) => {
  e.preventDefault();
  dragCounter.value--;
  if (dragCounter.value === 0) isDragOver.value = false;
};

const handleDragOver = (e: DragEvent) => {
  e.preventDefault();
};

const handleDrop = (e: DragEvent) => {
  e.preventDefault();
  dragCounter.value = 0;
  isDragOver.value = false;
  const files = e.dataTransfer?.files;
  if (files && files.length > 0) emit("upload", files);
};
</script>

<template>
  <div
    class="file-browser"
    :class="{ 'drag-over': isDragOver }"
    @dragenter="handleDragEnter"
    @dragleave="handleDragLeave"
    @dragover="handleDragOver"
    @drop="handleDrop"
  >
    <div v-if="isDragOver" class="drop-overlay">
      <div class="drop-message">
        <NIcon size="32"><PhUploadSimple weight="duotone" /></NIcon>
        <span>{{ t('files.drop_upload') }} {{ currentDir }}</span>
      </div>
    </div>
    <slot />
  </div>
</template>

<style scoped>
.file-browser {
  position: relative;
  border-radius: 8px;
  overflow: hidden;
  transition: all 0.2s ease;
}

.file-browser.drag-over {
  border: 2px dashed var(--accent);
  background: var(--accent-bg);
}

.drop-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(var(--accent-rgb), 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  backdrop-filter: blur(4px);
}

.drop-message {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: var(--accent);
  font-weight: 600;
  font-size: 16px;
}

@media (prefers-reduced-motion: reduce) {
  .file-browser {
    transition: none;
  }
}
</style>
