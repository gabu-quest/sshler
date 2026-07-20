import { render, fireEvent, waitFor } from "@testing-library/vue";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import DirectorySearchInput from "./DirectorySearchInput.vue";

// Mock API
vi.mock("@/api/http", () => ({
  searchDirectories: vi.fn(),
}));

import { searchDirectories } from "@/api/http";

// Mock naive-ui components
vi.mock("naive-ui", () => {
  const stub = (template: string) => ({ template });
  return {
    NInput: {
      template: `<div>
        <slot name="prefix" />
        <input
          :value="modelValue || value"
          @input="$emit('update:value', $event.target.value); $emit('update:modelValue', $event.target.value)"
          @focus="$emit('focus')"
          @blur="$emit('blur')"
          :placeholder="placeholder"
          data-testid="search-input"
        />
        <slot name="suffix" />
      </div>`,
      props: ["value", "modelValue", "placeholder", "size", "clearable"],
      emits: ["update:value", "update:modelValue", "focus", "blur"],
    },
    NIcon: stub("<span><slot /></span>"),
    NSpin: stub("<span data-testid='spinner'>Loading...</span>"),
    NEmpty: stub("<div data-testid='empty'>No results</div>"),
  };
});

describe("DirectorySearchInput", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders search input", () => {
    const { getByTestId } = render(DirectorySearchInput, {
      props: { box: "testbox", token: "test-token" },
    });

    expect(getByTestId("search-input")).toBeTruthy();
  });

  it("does not search with query shorter than 2 characters", async () => {
    const { getByTestId } = render(DirectorySearchInput, {
      props: { box: "testbox", token: "test-token" },
    });

    const input = getByTestId("search-input");
    await fireEvent.update(input, "a");

    // Fast-forward debounce timer
    vi.advanceTimersByTime(500);

    expect(searchDirectories).not.toHaveBeenCalled();
  });

  it("debounces API calls by 300ms", async () => {
    (searchDirectories as ReturnType<typeof vi.fn>).mockResolvedValue({
      box: "testbox",
      query: "pro",
      results: [],
    });

    const { getByTestId } = render(DirectorySearchInput, {
      props: { box: "testbox", token: "test-token" },
    });

    const input = getByTestId("search-input");

    // Type quickly
    await fireEvent.update(input, "p");
    vi.advanceTimersByTime(100);
    await fireEvent.update(input, "pr");
    vi.advanceTimersByTime(100);
    await fireEvent.update(input, "pro");
    vi.advanceTimersByTime(100);

    // Should not have called API yet (only 300ms since first, but query kept changing)
    expect(searchDirectories).not.toHaveBeenCalled();

    // Wait for debounce
    vi.advanceTimersByTime(300);

    // Now should have called once with final query
    await waitFor(() => {
      expect(searchDirectories).toHaveBeenCalledTimes(1);
      expect(searchDirectories).toHaveBeenCalledWith("testbox", "pro", "test-token");
    });
  });

  it("emits select event when result is clicked", async () => {
    (searchDirectories as ReturnType<typeof vi.fn>).mockResolvedValue({
      box: "testbox",
      query: "projects",
      results: [
        { path: "/home/user/projects", score: 10, source: "frecency" },
        { path: "/var/projects", score: 5, source: "discovery" },
      ],
    });

    const { getByTestId, getByText, emitted } = render(DirectorySearchInput, {
      props: { box: "testbox", token: "test-token" },
    });

    // Type query
    const input = getByTestId("search-input");
    await fireEvent.update(input, "projects");

    // Trigger debounce
    vi.advanceTimersByTime(300);

    // Wait for results to appear
    await waitFor(() => {
      expect(getByText("/home/user/projects")).toBeTruthy();
    });

    // Click a result
    await fireEvent.mouseDown(getByText("/home/user/projects"));

    // Check select event was emitted
    expect(emitted().select).toBeTruthy();
    expect(emitted().select[0]).toEqual(["/home/user/projects"]);
  });

  it("shows loading state while searching", async () => {
    vi.useRealTimers();

    let resolveSearch: (value: any) => void;
    const searchPromise = new Promise((resolve) => {
      resolveSearch = resolve;
    });
    (searchDirectories as ReturnType<typeof vi.fn>).mockReturnValue(searchPromise);

    const { getByTestId, getAllByTestId, container } = render(DirectorySearchInput, {
      props: { box: "testbox", token: "test-token" },
    });

    // Type query
    const input = getByTestId("search-input");
    await fireEvent.update(input, "test");

    // Wait for debounce (300ms) with real timers
    await waitFor(
      () => {
        expect(searchDirectories).toHaveBeenCalled();
      },
      { timeout: 1000 }
    );

    // Should show loading spinner(s) - one in suffix, one in dropdown
    await waitFor(
      () => {
        const spinners = getAllByTestId("spinner");
        expect(spinners.length).toBeGreaterThan(0);
      },
      { timeout: 1000 }
    );

    // Resolve the search
    resolveSearch!({
      box: "testbox",
      query: "test",
      results: [],
    });

    // Wait for loading to finish - spinners should be gone or reduced
    await waitFor(
      () => {
        const spinners = container.querySelectorAll('[data-testid="spinner"]');
        expect(spinners.length).toBe(0);
      },
      { timeout: 1000 }
    );

    vi.useFakeTimers();
  });

  it("clears results when query is cleared", async () => {
    (searchDirectories as ReturnType<typeof vi.fn>).mockResolvedValue({
      box: "testbox",
      query: "test",
      results: [{ path: "/test/path", score: 1, source: "frecency" }],
    });

    const { getByTestId, getByText, queryByText } = render(DirectorySearchInput, {
      props: { box: "testbox", token: "test-token" },
    });

    // Type query
    const input = getByTestId("search-input");
    await fireEvent.update(input, "test");
    vi.advanceTimersByTime(300);

    // Wait for results
    await waitFor(() => {
      expect(getByText("/test/path")).toBeTruthy();
    });

    // Clear input
    await fireEvent.update(input, "");

    // Results should be cleared
    await waitFor(() => {
      expect(queryByText("/test/path")).toBeFalsy();
    });
  });

  it("does not search without a box", async () => {
    const { getByTestId } = render(DirectorySearchInput, {
      props: { box: null, token: "test-token" },
    });

    const input = getByTestId("search-input");
    await fireEvent.update(input, "test");
    vi.advanceTimersByTime(300);

    expect(searchDirectories).not.toHaveBeenCalled();
  });
});
