import { render, fireEvent, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DiffNotebookDrawer from "./DiffNotebookDrawer.vue";

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  }));
}

vi.mock("@/api/http", () => ({
  listDiffNotebooks: vi.fn(),
  deleteDiffNotebook: vi.fn(),
}));

import { listDiffNotebooks, deleteDiffNotebook } from "@/api/http";

const mockedList = vi.mocked(listDiffNotebooks);
const mockedDelete = vi.mocked(deleteDiffNotebook);

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NDrawer: stub('<div class="stub-drawer" v-if="show"><slot /></div>', ["show"]),
    NDrawerContent: stub('<div class="stub-drawer-content" :data-title="title"><slot /></div>', ["title", "closable"]),
    NButton: stub(
      '<button class="stub-button" :data-testid="$attrs[`data-testid`]" :disabled="disabled" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
      ["disabled", "type", "size"],
    ),
    NIcon: stub('<span class="stub-icon"><slot /></span>'),
    NSpace: stub('<div class="stub-space"><slot /></div>'),
    NSpin: stub('<span class="stub-spin" data-testid="spin" />'),
    NTag: stub('<span class="stub-tag"><slot /></span>', ["type", "size"]),
    NPopconfirm: stub('<span class="stub-popconfirm" @click="$emit(\'positive-click\')"><slot name="trigger" /></span>'),
    NEmpty: stub('<div class="stub-empty" data-testid="empty">{{ description }}</div>', ["description", "size"]),
    useMessage: () => ({ success: vi.fn(), error: vi.fn() }),
  };
});

vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return {
    PhClockCounterClockwise: stub("PhClockCounterClockwise"),
    PhCloudArrowDown: stub("PhCloudArrowDown"),
    PhArrowsClockwise: stub("PhArrowsClockwise"),
    PhTrash: stub("PhTrash"),
    PhFloppyDisk: stub("PhFloppyDisk"),
  };
});

// Bootstrap store needs a token for the list call; mock the store module.
vi.mock("@/stores/bootstrap", () => ({
  useBootstrapStore: () => ({ token: "test-token" }),
}));

// History composable — use a real localStorage-backed instance.
vi.mock("@/composables/useDiffHistory", async () => {
  const actual = await vi.importActual("@/composables/useDiffHistory");
  return actual;
});

function makeMeta(id: string, label: string, ageSec = 60) {
  return {
    id,
    label,
    cell_count: 3,
    created_at: Date.now() / 1000 - ageSec,
    updated_at: Date.now() / 1000 - ageSec,
  };
}

describe("DiffNotebookDrawer", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    mockedList.mockReset();
    mockedDelete.mockReset();
    localStorage.clear();
  });

  it("renders the empty state when no server or local notebooks exist", async () => {
    mockedList.mockResolvedValueOnce({ notebooks: [] });
    const { getAllByTestId } = render(DiffNotebookDrawer, {
      props: { modelValue: true },
    });
    await waitFor(() => {
      const empties = getAllByTestId("empty");
      expect(empties.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("lists server notebooks from the API call", async () => {
    mockedList.mockResolvedValueOnce({
      notebooks: [makeMeta("abc12345", "ssh refactor"), makeMeta("def67890", "example port")],
    });
    const { findByTestId } = render(DiffNotebookDrawer, {
      props: { modelValue: true },
    });
    const first = await findByTestId("diff-saved-server-abc12345");
    expect(first.textContent).toContain("ssh refactor");
    const second = await findByTestId("diff-saved-server-def67890");
    expect(second.textContent).toContain("example port");
  });

  it("emits 'load-saved' with the id when the Load button is clicked", async () => {
    mockedList.mockResolvedValueOnce({
      notebooks: [makeMeta("abc12345", "ssh refactor")],
    });
    const { findByTestId, emitted } = render(DiffNotebookDrawer, {
      props: { modelValue: true },
    });
    const loadBtn = await findByTestId("diff-saved-load-abc12345");
    await fireEvent.click(loadBtn);
    const events = emitted()["load-saved"] ?? [];
    expect(events.length).toBeGreaterThanOrEqual(1);
    expect((events[0] as unknown[])[0]).toBe("abc12345");
  });

  it("calls deleteDiffNotebook when the popconfirm fires", async () => {
    mockedList.mockResolvedValueOnce({
      notebooks: [makeMeta("abc12345", "ssh refactor")],
    });
    mockedDelete.mockResolvedValueOnce({ ok: true, removed: true });
    const { findByTestId } = render(DiffNotebookDrawer, {
      props: { modelValue: true },
    });
    // The popconfirm stub forwards click → positive-click; clicking the wrapped
    // trigger button triggers the delete handler.
    const delBtn = await findByTestId("diff-saved-delete-abc12345");
    await fireEvent.click(delBtn);
    await waitFor(() => {
      expect(mockedDelete).toHaveBeenCalledTimes(1);
    });
    // First arg is the id; second is the token from the mocked bootstrap store.
    expect(mockedDelete.mock.calls[0]![0]).toBe("abc12345");
  });
});
