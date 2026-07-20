import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/http", () => ({
  fetchClaudeSessions: vi.fn(),
}));

import { fetchClaudeSessions } from "@/api/http";
import type { ClaudeSession } from "@/api/types";
import { useClaudeSessionsStore } from "@/stores/claudeSessions";

const mockFetch = vi.mocked(fetchClaudeSessions);

function makeSession(id: string, over: Partial<ClaudeSession> = {}): ClaudeSession {
  return {
    id,
    cwd: "/proj",
    title: `Title ${id}`,
    last_prompt: null,
    last_active: 1_700_000_000,
    git_branch: null,
    version: null,
    size_bytes: 100,
    project_dir: "-proj",
    ...over,
  };
}

describe("claudeSessions store", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    mockFetch.mockReset();
  });

  it("load populates sessions in order and marks loaded", async () => {
    mockFetch.mockResolvedValue([
      makeSession("a", { title: "Alpha" }),
      makeSession("b", { title: "Beta" }),
    ]);
    const store = useClaudeSessionsStore();

    await store.load("tok");

    expect(mockFetch).toHaveBeenCalledWith("tok");
    expect(store.sessions).toHaveLength(2);
    expect(store.sessions[0].title).toBe("Alpha");
    expect(store.sessions[1].title).toBe("Beta");
    expect(store.loaded).toBe(true);
    expect(store.error).toBeNull();
    expect(store.loading).toBe(false);
  });

  it("clears sessions and records the error on failure", async () => {
    const store = useClaudeSessionsStore();

    mockFetch.mockResolvedValueOnce([makeSession("a", { title: "Alpha" })]);
    await store.load(null);
    expect(store.sessions).toHaveLength(1);

    mockFetch.mockRejectedValueOnce(new Error("boom"));
    await store.refresh(null);

    expect(store.error).toBe("boom");
    expect(store.sessions).toEqual([]);
    expect(store.loading).toBe(false);
  });

  it("templateFor resolves override over global over default", () => {
    const store = useClaudeSessionsStore();
    expect(store.templateFor("a")).toBe("claude --resume {id}");

    store.setGlobalTemplate("claudeee --resume {id}");
    expect(store.templateFor("a")).toBe("claudeee --resume {id}");

    store.setOverride("a", "claude --resume {id} --model opus");
    expect(store.templateFor("a")).toBe("claude --resume {id} --model opus");
    expect(store.templateFor("b")).toBe("claudeee --resume {id}");
  });

  it("setGlobalTemplate persists; blank resets to default", () => {
    const store = useClaudeSessionsStore();
    store.setGlobalTemplate("claudeee --resume {id}");
    expect(localStorage.getItem("sshler:claude:resumeTemplate")).toBe("claudeee --resume {id}");

    store.setGlobalTemplate("   ");
    expect(store.resumeTemplate).toBe("claude --resume {id}");
  });

  it("setOverride sets, persists, and clears", () => {
    const store = useClaudeSessionsStore();
    store.setOverride("a", "x --resume {id}");
    expect(store.hasOverride("a")).toBe(true);
    expect(JSON.parse(localStorage.getItem("sshler:claude:resumeOverrides")!)).toEqual({
      a: "x --resume {id}",
    });

    store.setOverride("a", "");
    expect(store.hasOverride("a")).toBe(false);
    expect(JSON.parse(localStorage.getItem("sshler:claude:resumeOverrides")!)).toEqual({});
  });
});
