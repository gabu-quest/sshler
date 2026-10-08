import { computed, ref } from "vue";
import { defineStore } from "pinia";

import {
  createArtifact as apiCreateArtifact,
  createArtifactProject as apiCreateProject,
  deleteArtifact as apiDeleteArtifact,
  deleteArtifactProject as apiDeleteProject,
  fetchArtifactPages as apiFetchPages,
  fetchArtifactProjects as apiFetchProjects,
  fetchArtifacts as apiFetchArtifacts,
  renameArtifactProject as apiRenameProject,
  rescanArtifact as apiRescanArtifact,
  updateArtifact as apiUpdateArtifact,
} from "@/api/http";
import type { ArtifactFilters } from "@/api/http";
import type {
  ArtifactPage,
  ArtifactProject,
  ArtifactRegistration,
} from "@/api/types";

export const useArtifactsStore = defineStore("artifacts", () => {
  const projects = ref<ArtifactProject[]>([]);
  const artifacts = ref<ArtifactRegistration[]>([]);
  const pagesByArtifact = ref<Record<string, ArtifactPage[]>>({});
  const loadingPages = ref<Set<string>>(new Set());
  const loading = ref(false);
  const error = ref<string | null>(null);
  const currentFilters = ref<ArtifactFilters>({});

  const artifactsByProject = computed(() => {
    const grouped: Record<string, ArtifactRegistration[]> = {};
    for (const artifact of artifacts.value) {
      (grouped[artifact.project_id] ??= []).push(artifact);
    }
    for (const items of Object.values(grouped)) {
      items.sort((a, b) =>
        `${a.group_path}/${a.title || a.source_path}`.localeCompare(
          `${b.group_path}/${b.title || b.source_path}`,
        ),
      );
    }
    return grouped;
  });

  async function refresh(
    token: string | null,
    filters: ArtifactFilters = currentFilters.value,
  ): Promise<void> {
    currentFilters.value = { ...filters };
    loading.value = true;
    error.value = null;
    try {
      const [projectResult, artifactResult] = await Promise.all([
        apiFetchProjects(token),
        apiFetchArtifacts(token, filters),
      ]);
      projects.value = [...projectResult.projects].sort((a, b) => a.name.localeCompare(b.name));
      artifacts.value = artifactResult.artifacts;
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason);
      throw reason;
    } finally {
      loading.value = false;
    }
  }

  async function ensurePages(id: string, token: string | null): Promise<ArtifactPage[]> {
    if (pagesByArtifact.value[id]) return pagesByArtifact.value[id];
    loadingPages.value.add(id);
    try {
      const result = await apiFetchPages(id, token);
      pagesByArtifact.value[id] = result.pages;
      return result.pages;
    } finally {
      loadingPages.value.delete(id);
    }
  }

  async function rescan(id: string, token: string | null): Promise<ArtifactPage[]> {
    loadingPages.value.add(id);
    try {
      const result = await apiRescanArtifact(id, token);
      pagesByArtifact.value[id] = result.pages;
      return result.pages;
    } finally {
      loadingPages.value.delete(id);
    }
  }

  async function create(body: Record<string, unknown>, token: string | null) {
    const result = await apiCreateArtifact(body, token);
    await refresh(token);
    return result;
  }

  async function update(
    id: string,
    body: Record<string, unknown>,
    token: string | null,
  ) {
    const result = await apiUpdateArtifact(id, body, token);
    delete pagesByArtifact.value[id];
    await refresh(token);
    return result;
  }

  async function remove(id: string, token: string | null): Promise<boolean> {
    const result = await apiDeleteArtifact(id, token);
    delete pagesByArtifact.value[id];
    artifacts.value = artifacts.value.filter((artifact) => artifact.id !== id);
    await refresh(token);
    return result.removed;
  }

  async function createProject(name: string, token: string | null) {
    const project = await apiCreateProject(name, token);
    await refresh(token);
    return project;
  }

  async function renameProject(id: string, name: string, token: string | null) {
    const project = await apiRenameProject(id, name, token);
    await refresh(token);
    return project;
  }

  async function removeProject(id: string, token: string | null) {
    const result = await apiDeleteProject(id, true, token);
    pagesByArtifact.value = {};
    await refresh(token);
    return result;
  }

  return {
    projects,
    artifacts,
    pagesByArtifact,
    loadingPages,
    loading,
    error,
    artifactsByProject,
    refresh,
    ensurePages,
    rescan,
    create,
    update,
    remove,
    createProject,
    renameProject,
    removeProject,
  };
});
