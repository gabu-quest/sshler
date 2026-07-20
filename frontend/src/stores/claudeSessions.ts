import { ref } from "vue";

import { defineStore } from "pinia";

import { fetchClaudeSessions } from "@/api/http";
import type { ClaudeSession } from "@/api/types";

/** Default resume command. `{id}` is replaced server-side with the session UUID. */
export const DEFAULT_RESUME_TEMPLATE = "claude --resume {id}";
const TEMPLATE_KEY = "sshler:claude:resumeTemplate";
const OVERRIDES_KEY = "sshler:claude:resumeOverrides";

function loadTemplate(): string {
  try {
    return localStorage.getItem(TEMPLATE_KEY) || DEFAULT_RESUME_TEMPLATE;
  } catch {
    return DEFAULT_RESUME_TEMPLATE;
  }
}

function loadOverrides(): Record<string, string> {
  try {
    const raw = localStorage.getItem(OVERRIDES_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? (parsed as Record<string, string>) : {};
  } catch {
    return {};
  }
}

/**
 * Claude session dashboard store.
 *
 * Pull-based: the data source is the local filesystem (~/.claude transcripts),
 * so there's no WebSocket — the view loads on mount and refreshes on demand
 * (button + window focus). Deliberately simpler than the progress/ping stores.
 */
export const useClaudeSessionsStore = defineStore("claudeSessions", () => {
  const sessions = ref<ClaudeSession[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const loaded = ref(false);

  // Resume-command config (persisted to localStorage). Global default + per-session
  // overrides. The resolved template is sent to the open endpoint, which substitutes
  // the validated session id for `{id}`.
  const resumeTemplate = ref<string>(loadTemplate());
  const resumeOverrides = ref<Record<string, string>>(loadOverrides());

  /** Effective template for a session: per-session override → global → default. */
  function templateFor(id: string): string {
    return resumeOverrides.value[id] || resumeTemplate.value || DEFAULT_RESUME_TEMPLATE;
  }

  function setGlobalTemplate(value: string): void {
    resumeTemplate.value = value.trim() || DEFAULT_RESUME_TEMPLATE;
    try {
      localStorage.setItem(TEMPLATE_KEY, resumeTemplate.value);
    } catch {
      /* ignore persistence failures */
    }
  }

  /** Set (trimmed) or clear (empty/null) a per-session override. */
  function setOverride(id: string, value: string | null): void {
    const next = { ...resumeOverrides.value };
    const trimmed = value?.trim();
    if (trimmed) {
      next[id] = trimmed;
    } else {
      delete next[id];
    }
    resumeOverrides.value = next;
    try {
      localStorage.setItem(OVERRIDES_KEY, JSON.stringify(next));
    } catch {
      /* ignore persistence failures */
    }
  }

  function hasOverride(id: string): boolean {
    return id in resumeOverrides.value;
  }

  async function load(token: string | null): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      sessions.value = await fetchClaudeSessions(token);
      loaded.value = true;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
      sessions.value = [];
    } finally {
      loading.value = false;
    }
  }

  /** Alias for an explicit user-triggered reload (same behavior as load). */
  async function refresh(token: string | null): Promise<void> {
    await load(token);
  }

  return {
    sessions,
    loading,
    error,
    loaded,
    load,
    refresh,
    resumeTemplate,
    resumeOverrides,
    templateFor,
    setGlobalTemplate,
    setOverride,
    hasOverride,
  };
});
