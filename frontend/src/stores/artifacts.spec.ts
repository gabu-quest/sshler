import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ArtifactProject, ArtifactRegistration } from "@/api/types";

vi.mock("@/api/http", () => ({
  createArtifact: vi.fn(),
  createArtifactProject: vi.fn(),
  deleteArtifact: vi.fn(),
  deleteArtifactProject: vi.fn(),
  fetchArtifactPages: vi.fn(),
  fetchArtifactProjects: vi.fn(),
  fetchArtifacts: vi.fn(),
  renameArtifactProject: vi.fn(),
  rescanArtifact: vi.fn(),
  updateArtifact: vi.fn(),
}));

import {
  deleteArtifact,
  fetchArtifactPages,
  fetchArtifactProjects,
  fetchArtifacts,
  rescanArtifact,
} from "@/api/http";
import { useArtifactsStore } from "./artifacts";

const projects: ArtifactProject[] = [
  { id: "p2", name: "Second", slug: "second", registration_count: 1, created_at: 1, updated_at: 1 },
  { id: "p1", name: "First", slug: "first", registration_count: 2, created_at: 1, updated_at: 1 },
];

const artifacts: ArtifactRegistration[] = [
  {
    id: "a2",
    project_id: "p1",
    project_name: "First",
    project_slug: "first",
    group_path: "design/final",
    source_path: "./fixtures/site-b",
    mode: "site",
    entrypoint: "index.html",
    title: "Beta",
    slug: "beta",
    alias_path: "/r/first/beta/",
    mount_path: null,
    exists: true,
    created_at: 1,
    updated_at: 1,
  },
  {
    id: "a1",
    project_id: "p1",
    project_name: "First",
    project_slug: "first",
    group_path: "design/drafts",
    source_path: "./fixtures/page-a.html",
    mode: "file",
    entrypoint: null,
    title: "Alpha",
    slug: "alpha",
    alias_path: "/r/first/alpha/",
    mount_path: null,
    exists: true,
    created_at: 1,
    updated_at: 1,
  },
];

describe("artifacts store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(fetchArtifactProjects).mockResolvedValue({ projects });
    vi.mocked(fetchArtifacts).mockResolvedValue({ artifacts });
  });

  it("refreshes and groups registrations by required project", async () => {
    const store = useArtifactsStore();
    await store.refresh("token");

    expect(store.projects.map((project) => project.name)).toEqual(["First", "Second"]);
    expect(store.artifactsByProject.p1?.map((artifact) => artifact.id)).toEqual(["a1", "a2"]);
  });

  it("caches page discovery and replaces it on rescan", async () => {
    vi.mocked(fetchArtifactPages).mockResolvedValue({
      pages: [{ relative_path: "", title: "First", group_path: "", serve_path: "/a/a1/" }],
    });
    vi.mocked(rescanArtifact).mockResolvedValue({
      pages: [
        { relative_path: "", title: "First", group_path: "", serve_path: "/a/a1/" },
        {
          relative_path: "details.html",
          title: "Details",
          group_path: "",
          serve_path: "/a/a1/details.html",
        },
      ],
    });
    const store = useArtifactsStore();

    await store.ensurePages("a1", null);
    await store.ensurePages("a1", null);
    expect(fetchArtifactPages).toHaveBeenCalledTimes(1);

    const pages = await store.rescan("a1", null);
    expect(pages).toHaveLength(2);
    expect(store.pagesByArtifact.a1).toHaveLength(2);
  });

  it("unregisters catalog metadata and refreshes the catalog", async () => {
    vi.mocked(deleteArtifact).mockResolvedValue({ ok: true, removed: true });
    const store = useArtifactsStore();
    await store.refresh(null);
    store.pagesByArtifact.a1 = [
      { relative_path: "", title: "First", group_path: "", serve_path: "/a/a1/" },
    ];

    expect(await store.remove("a1", null)).toBe(true);
    expect(deleteArtifact).toHaveBeenCalledWith("a1", null);
    expect(store.pagesByArtifact.a1).toBeUndefined();
    expect(fetchArtifacts).toHaveBeenCalledTimes(2);
  });
});
