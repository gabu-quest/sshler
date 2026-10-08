<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  NAlert,
  NButton,
  NCard,
  NEmpty,
  NIcon,
  NInput,
  NModal,
  NSelect,
  NSpin,
  NTag,
  useDialog,
  useMessage,
} from "naive-ui";
import {
  PhArrowClockwise,
  PhCopy,
  PhFolderOpen,
  PhGlobe,
  PhPencilSimple,
  PhPlus,
  PhMagnifyingGlass,
  PhTrash,
} from "@phosphor-icons/vue";

import type { ArtifactMode, ArtifactPage, ArtifactProject, ArtifactRegistration } from "@/api/types";
import { useI18n } from "@/i18n";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useArtifactsStore } from "@/stores/artifacts";
import { buildArtifactUrl } from "@/utils/artifactUrl";

const store = useArtifactsStore();
const bootstrap = useBootstrapStore();
const route = useRoute();
const router = useRouter();
const message = useMessage();
const dialog = useDialog();
const { t } = useI18n();

const token = computed(() => bootstrap.token || bootstrap.payload?.token || null);
const artifactPort = computed(() => bootstrap.payload?.artifact_server_port ?? null);
const localServingAvailable = computed(() =>
  buildArtifactUrl("/", artifactPort.value) !== null,
);

const registrationModal = ref(false);
const editingArtifactId = ref<string | null>(null);
const projectModal = ref(false);
const editingProject = ref<ArtifactProject | null>(null);
const projectName = ref("");
const previewUrl = ref<string | null>(null);
const expandedArtifacts = ref<Set<string>>(new Set());
const submitting = ref(false);
const searchQuery = ref("");
const projectFilter = ref<string | null>(null);
const modeFilter = ref<ArtifactMode | null>(null);
let searchTimer: ReturnType<typeof setTimeout> | null = null;

const form = reactive({
  source_path: "",
  project: "",
  group_path: "",
  mode: "auto" as ArtifactMode | "auto",
  entrypoint: "",
  title: "",
  slug: "",
  mount_path: "",
});

const modeOptions = [
  { label: t("artifacts.mode.auto"), value: "auto" },
  { label: t("artifacts.mode.file"), value: "file" },
  { label: t("artifacts.mode.site"), value: "site" },
  { label: t("artifacts.mode.collection"), value: "collection" },
];
const filterModeOptions = modeOptions.filter((option) => option.value !== "auto");
const projectOptions = computed(() =>
  store.projects.map((project) => ({ label: project.name, value: project.slug })),
);
const filtering = computed(
  () => Boolean(searchQuery.value.trim() || projectFilter.value || modeFilter.value),
);
const visibleProjects = computed(() =>
  filtering.value
    ? store.projects.filter((project) => store.artifactsByProject[project.id]?.length)
    : store.projects,
);

function basename(path: string): string {
  return path.split(/[/\\]/).filter(Boolean).pop() || path;
}

function displayTitle(artifact: ArtifactRegistration): string {
  return artifact.title || basename(artifact.source_path);
}

function resetForm(): void {
  editingArtifactId.value = null;
  Object.assign(form, {
    source_path: "",
    project: "",
    group_path: "",
    mode: "auto",
    entrypoint: "",
    title: "",
    slug: "",
    mount_path: "",
  });
}

function openRegister(): void {
  resetForm();
  registrationModal.value = true;
}

function openEdit(artifact: ArtifactRegistration): void {
  editingArtifactId.value = artifact.id;
  Object.assign(form, {
    source_path: artifact.source_path,
    project: artifact.project_name,
    group_path: artifact.group_path,
    mode: artifact.mode,
    entrypoint: artifact.entrypoint || "",
    title: artifact.title || "",
    slug: artifact.slug,
    mount_path: artifact.mount_path?.replace(/^\//, "") || "",
  });
  registrationModal.value = true;
}

async function submitRegistration(): Promise<void> {
  submitting.value = true;
  try {
    const body: Record<string, unknown> = {
      source_path: form.source_path,
      project: form.project || undefined,
      group_path: form.group_path,
      mode: form.mode,
      entrypoint: editingArtifactId.value ? form.entrypoint || null : form.entrypoint || undefined,
      title: editingArtifactId.value ? form.title || null : form.title || undefined,
      slug: editingArtifactId.value ? form.slug : form.slug || undefined,
      mount_path: editingArtifactId.value
        ? form.mount_path || null
        : form.mount_path || undefined,
    };
    if (editingArtifactId.value) {
      await store.update(editingArtifactId.value, body, token.value);
      message.success(t("artifacts.updated"));
    } else {
      const result = await store.create(body, token.value);
      message.success(
        result.created ? t("artifacts.registered") : t("artifacts.already_registered"),
      );
    }
    registrationModal.value = false;
    resetForm();
  } catch (reason) {
    message.error(reason instanceof Error ? reason.message : String(reason));
  } finally {
    submitting.value = false;
  }
}

async function togglePages(artifact: ArtifactRegistration): Promise<void> {
  const next = new Set(expandedArtifacts.value);
  if (next.has(artifact.id)) {
    next.delete(artifact.id);
    expandedArtifacts.value = next;
    return;
  }
  try {
    await store.ensurePages(artifact.id, token.value);
    next.add(artifact.id);
    expandedArtifacts.value = next;
  } catch (reason) {
    message.error(reason instanceof Error ? reason.message : String(reason));
  }
}

function urlFor(path: string): string | null {
  return buildArtifactUrl(path, artifactPort.value);
}

function openPage(page: ArtifactPage): void {
  const url = urlFor(page.serve_path);
  if (!url) return;
  const opened = window.open(url, "_blank", "noopener,noreferrer");
  if (opened) opened.opener = null;
}

async function previewArtifact(artifact: ArtifactRegistration): Promise<void> {
  try {
    const pages = await store.ensurePages(artifact.id, token.value);
    const url = pages[0] ? urlFor(pages[0].serve_path) : null;
    if (!url) {
      message.warning(t("artifacts.local_only"));
      return;
    }
    previewUrl.value = url;
  } catch (reason) {
    message.error(reason instanceof Error ? reason.message : String(reason));
  }
}

async function rescan(artifact: ArtifactRegistration): Promise<void> {
  try {
    const pages = await store.rescan(artifact.id, token.value);
    expandedArtifacts.value = new Set([...expandedArtifacts.value, artifact.id]);
    message.success(t("artifacts.rescanned", { n: pages.length }));
  } catch (reason) {
    message.error(reason instanceof Error ? reason.message : String(reason));
  }
}

function unregister(artifact: ArtifactRegistration): void {
  dialog.warning({
    title: t("artifacts.unregister"),
    content: t("artifacts.unregister_confirm", { title: displayTitle(artifact) }),
    positiveText: t("artifacts.unregister"),
    negativeText: t("common.cancel"),
    onPositiveClick: async () => {
      await store.remove(artifact.id, token.value);
      message.success(t("artifacts.unregistered"));
    },
  });
}

async function copyArtifactLink(artifact: ArtifactRegistration): Promise<void> {
  const url =
    urlFor(artifact.mount_path || artifact.alias_path) ||
    `${window.location.origin}/app/artifacts/${artifact.id}`;
  await navigator.clipboard.writeText(url);
  message.success(t("artifacts.link_copied"));
}

async function applyFilters(): Promise<void> {
  await store.refresh(token.value, {
    q: searchQuery.value.trim() || undefined,
    project: projectFilter.value || undefined,
    mode: modeFilter.value || undefined,
  });
}

watch([searchQuery, projectFilter, modeFilter], () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    applyFilters().catch((reason) => {
      message.error(reason instanceof Error ? reason.message : String(reason));
    });
  }, 220);
});

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer);
});

function openProjectDialog(project?: ArtifactProject): void {
  editingProject.value = project || null;
  projectName.value = project?.name || "";
  projectModal.value = true;
}

async function saveProject(): Promise<void> {
  try {
    if (editingProject.value) {
      await store.renameProject(editingProject.value.id, projectName.value, token.value);
    } else {
      await store.createProject(projectName.value, token.value);
    }
    projectModal.value = false;
    message.success(t("artifacts.project_saved"));
  } catch (reason) {
    message.error(reason instanceof Error ? reason.message : String(reason));
  }
}

function removeProject(project: ArtifactProject): void {
  dialog.warning({
    title: t("artifacts.project_remove"),
    content: t("artifacts.project_remove_confirm", {
      name: project.name,
      n: project.registration_count,
    }),
    positiveText: t("artifacts.project_remove"),
    negativeText: t("common.cancel"),
    onPositiveClick: async () => {
      await store.removeProject(project.id, token.value);
      message.success(t("artifacts.project_removed"));
    },
  });
}

onMounted(async () => {
  if (!bootstrap.payload) await bootstrap.bootstrap();
  await store.refresh(token.value);
  const selected = typeof route.params.id === "string" ? route.params.id : null;
  if (selected) {
    const artifact = store.artifacts.find((item) => item.id === selected);
    if (artifact) {
      await togglePages(artifact);
      router.replace("/artifacts");
    }
  }
});
</script>

<template>
  <div class="artifacts-view">
    <div class="page-header">
      <div>
        <h1>{{ t("artifacts.title") }}</h1>
        <p>{{ t("artifacts.subtitle") }}</p>
      </div>
      <div class="header-actions">
        <NButton secondary @click="openProjectDialog()">
          <template #icon><NIcon><PhFolderOpen /></NIcon></template>
          {{ t("artifacts.new_project") }}
        </NButton>
        <NButton type="primary" @click="openRegister">
          <template #icon><NIcon><PhPlus /></NIcon></template>
          {{ t("artifacts.register") }}
        </NButton>
      </div>
    </div>

    <NAlert v-if="!localServingAvailable" type="warning" class="local-alert">
      {{ t("artifacts.local_only") }}
    </NAlert>
    <NAlert v-if="store.error" type="error" class="local-alert">{{ store.error }}</NAlert>

    <div class="catalog-filters">
      <NInput
        v-model:value="searchQuery"
        clearable
        :placeholder="t('artifacts.search_placeholder')"
      >
        <template #prefix><NIcon><PhMagnifyingGlass /></NIcon></template>
      </NInput>
      <NSelect
        v-model:value="projectFilter"
        clearable
        :options="projectOptions"
        :placeholder="t('artifacts.all_projects')"
      />
      <NSelect
        v-model:value="modeFilter"
        clearable
        :options="filterModeOptions"
        :placeholder="t('artifacts.all_modes')"
      />
    </div>

    <NSpin :show="store.loading">
      <NEmpty
        v-if="!store.loading && store.projects.length === 0"
        :description="t('artifacts.empty')"
      >
        <template #extra>
          <NButton type="primary" @click="openRegister">{{ t("artifacts.register") }}</NButton>
        </template>
      </NEmpty>

      <NEmpty
        v-if="!store.loading && filtering && store.artifacts.length === 0"
        :description="t('artifacts.no_matches')"
      />

      <section v-for="project in visibleProjects" :key="project.id" class="project-section">
        <div class="project-header">
          <div>
            <h2>{{ project.name }}</h2>
            <span>{{ t("artifacts.registration_count", { n: project.registration_count }) }}</span>
          </div>
          <div>
            <NButton quaternary circle @click="openProjectDialog(project)" :title="t('common.rename')">
              <NIcon><PhPencilSimple /></NIcon>
            </NButton>
            <NButton
              quaternary
              circle
              type="error"
              @click="removeProject(project)"
              :title="t('artifacts.project_remove')"
            >
              <NIcon><PhTrash /></NIcon>
            </NButton>
          </div>
        </div>

        <NEmpty
          v-if="!(store.artifactsByProject[project.id]?.length)"
          :description="t('artifacts.project_empty')"
          size="small"
        />

        <div
          v-for="artifact in store.artifactsByProject[project.id] || []"
          :key="artifact.id"
          class="artifact-indent"
          :style="{ '--depth': String((artifact.group_path.match(/\//g) || []).length) }"
        >
          <div v-if="artifact.group_path" class="group-path">
            {{ artifact.group_path.split('/').join('  ›  ') }}
          </div>
          <NCard
            size="small"
            class="artifact-card"
            role="button"
            tabindex="0"
            @click="togglePages(artifact)"
            @keydown.enter.prevent="togglePages(artifact)"
            @keydown.space.prevent="togglePages(artifact)"
          >
            <div class="artifact-row">
              <div class="artifact-details">
                <div class="artifact-title">
                  <span>{{ displayTitle(artifact) }}</span>
                  <NTag size="small">{{ artifact.mode }}</NTag>
                  <NTag size="small" :type="artifact.exists ? 'success' : 'error'">
                    {{ artifact.exists ? t("artifacts.ready") : t("artifacts.missing") }}
                  </NTag>
                </div>
                <code>{{ artifact.source_path }}</code>
                <code class="artifact-alias">{{ artifact.mount_path || artifact.alias_path }}</code>
              </div>
              <div class="artifact-actions" @click.stop @keydown.stop>
                <NButton size="small" @click="togglePages(artifact)">
                  {{ expandedArtifacts.has(artifact.id) ? t("common.close") : t("artifacts.pages") }}
                </NButton>
                <NButton size="small" :disabled="!artifact.exists" @click="previewArtifact(artifact)">
                  <template #icon><NIcon><PhGlobe /></NIcon></template>
                  {{ t("common.preview") }}
                </NButton>
                <NButton size="small" @click="copyArtifactLink(artifact)">
                  <template #icon><NIcon><PhCopy /></NIcon></template>
                </NButton>
                <NButton size="small" @click="rescan(artifact)">
                  <template #icon><NIcon><PhArrowClockwise /></NIcon></template>
                </NButton>
                <NButton size="small" @click="openEdit(artifact)">
                  <template #icon><NIcon><PhPencilSimple /></NIcon></template>
                </NButton>
                <NButton size="small" type="error" @click="unregister(artifact)">
                  <template #icon><NIcon><PhTrash /></NIcon></template>
                </NButton>
              </div>
            </div>
            <div
              v-if="expandedArtifacts.has(artifact.id)"
              class="page-list"
              @click.stop
              @keydown.stop
            >
              <NSpin v-if="store.loadingPages.has(artifact.id)" size="small" />
              <NEmpty
                v-else-if="!(store.pagesByArtifact[artifact.id]?.length)"
                :description="t('artifacts.no_pages')"
                size="small"
              />
              <button
                v-for="page in store.pagesByArtifact[artifact.id] || []"
                :key="page.relative_path"
                class="page-link"
                :disabled="!localServingAvailable"
                @click="openPage(page)"
              >
                <span>{{ page.title }}</span>
                <code>{{ page.relative_path || artifact.entrypoint || basename(artifact.source_path) }}</code>
              </button>
            </div>
          </NCard>
        </div>
      </section>
    </NSpin>

    <NModal
      v-model:show="registrationModal"
      preset="card"
      :title="editingArtifactId ? t('artifacts.edit') : t('artifacts.register')"
      class="form-modal"
    >
      <div class="form-grid">
        <label>
          <span>{{ t("artifacts.source") }}</span>
          <NInput v-model:value="form.source_path" :placeholder="t('artifacts.source_placeholder')" />
        </label>
        <label>
          <span>{{ t("artifacts.project") }}</span>
          <NInput v-model:value="form.project" :placeholder="t('artifacts.project_hint')" />
        </label>
        <label>
          <span>{{ t("artifacts.group") }}</span>
          <NInput v-model:value="form.group_path" placeholder="design/experiments" />
        </label>
        <label>
          <span>{{ t("artifacts.mode") }}</span>
          <NSelect v-model:value="form.mode" :options="modeOptions" />
        </label>
        <label v-if="form.mode === 'site' || form.mode === 'auto'">
          <span>{{ t("artifacts.entrypoint") }}</span>
          <NInput v-model:value="form.entrypoint" placeholder="index.html" />
        </label>
        <label>
          <span>{{ t("artifacts.title_override") }}</span>
          <NInput v-model:value="form.title" :placeholder="t('artifacts.title_hint')" />
        </label>
        <label>
          <span>{{ t("artifacts.slug") }}</span>
          <NInput v-model:value="form.slug" placeholder="quarterly-summary" />
        </label>
        <label>
          <span>{{ t("artifacts.mount") }}</span>
          <NInput v-model:value="form.mount_path" placeholder="published/reports" />
        </label>
      </div>
      <NAlert v-if="form.mode !== 'file'" type="info" class="scope-note">
        {{ t("artifacts.directory_scope") }}
      </NAlert>
      <div class="modal-actions">
        <NButton @click="registrationModal = false">{{ t("common.cancel") }}</NButton>
        <NButton
          type="primary"
          :loading="submitting"
          :disabled="!form.source_path"
          @click="submitRegistration"
        >
          {{ t("common.save") }}
        </NButton>
      </div>
    </NModal>

    <NModal
      v-model:show="projectModal"
      preset="card"
      :title="editingProject ? t('artifacts.rename_project') : t('artifacts.new_project')"
      class="project-modal"
    >
      <NInput v-model:value="projectName" :placeholder="t('artifacts.project')" />
      <div class="modal-actions">
        <NButton @click="projectModal = false">{{ t("common.cancel") }}</NButton>
        <NButton type="primary" :disabled="!projectName.trim()" @click="saveProject">
          {{ t("common.save") }}
        </NButton>
      </div>
    </NModal>

    <NModal
      :show="Boolean(previewUrl)"
      preset="card"
      :title="t('artifacts.preview_title')"
      class="preview-modal"
      @update:show="(show: boolean) => { if (!show) previewUrl = null }"
    >
      <iframe
        v-if="previewUrl"
        :src="previewUrl"
        sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-modals allow-popups"
        referrerpolicy="no-referrer"
        class="artifact-frame"
      />
    </NModal>
  </div>
</template>

<style scoped>
.artifacts-view { max-width: 1180px; margin: 0 auto; }
.page-header, .project-header, .artifact-row, .header-actions, .artifact-actions, .modal-actions {
  display: flex;
  align-items: center;
}
.page-header, .project-header, .artifact-row { justify-content: space-between; gap: 16px; }
.page-header h1, .project-header h2 { margin: 0; }
.page-header p, .project-header span { margin: 4px 0 0; color: var(--muted); }
.header-actions, .artifact-actions, .modal-actions { gap: 8px; flex-wrap: wrap; }
.local-alert { margin: 14px 0; }
.catalog-filters { display: grid; grid-template-columns: minmax(240px, 1fr) 220px 180px; gap: 10px; margin: 16px 0; }
.project-section { margin-top: 24px; }
.project-header { border-bottom: 1px solid var(--stroke); padding: 0 4px 10px; }
.artifact-indent { margin: 12px 0 0 calc(var(--depth) * 14px); }
.group-path { color: var(--muted); font-size: 12px; margin: 0 0 4px 8px; }
.artifact-card { border-left: 3px solid var(--accent); cursor: pointer; }
.artifact-card:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.artifact-details { min-width: 0; flex: 1; }
.artifact-title { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-weight: 700; }
.artifact-details code, .page-link code { display: block; color: var(--muted); overflow-wrap: anywhere; }
.artifact-details .artifact-alias { color: var(--accent); }
.page-list { display: grid; gap: 6px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--stroke); }
.page-link { text-align: left; border: 1px solid var(--stroke); border-radius: 8px; padding: 9px 11px; background: var(--panel-bg); color: var(--text); cursor: pointer; }
.page-link:hover { border-color: var(--accent); }
.page-link:disabled { opacity: .5; cursor: not-allowed; }
.form-modal { width: min(680px, 92vw); }
.project-modal { width: min(460px, 92vw); }
.preview-modal { width: min(1200px, 96vw); }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.form-grid label { display: grid; gap: 6px; }
.scope-note { margin-top: 14px; }
.modal-actions { justify-content: flex-end; margin-top: 18px; }
.artifact-frame { width: 100%; height: min(75vh, 820px); border: 0; border-radius: 8px; background: white; }
@media (max-width: 768px) {
  .page-header, .artifact-row { align-items: stretch; flex-direction: column; }
  .form-grid { grid-template-columns: 1fr; }
  .catalog-filters { grid-template-columns: 1fr; }
  .artifact-indent { margin-left: 0; }
}
</style>
