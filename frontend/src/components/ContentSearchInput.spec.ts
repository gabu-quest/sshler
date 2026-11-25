import { fireEvent, render, waitFor } from "@testing-library/vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import ContentSearchInput from "./ContentSearchInput.vue";

vi.mock("@/api/http", () => ({
  grepContent: vi.fn(),
}));

import { grepContent } from "@/api/http";

vi.mock("naive-ui", () => ({
  NInput: {
    template: `<input :value="value" @input="$emit('update:value', $event.target.value)" data-testid="search-input" />`,
    props: ["value", "placeholder", "size", "clearable"],
    emits: ["update:value", "focus", "blur"],
  },
  NIcon: { template: "<span><slot /></span>" },
  NSpin: { template: "<span data-testid='spinner' />" },
  NEmpty: { props: ["description"], template: "<div data-testid='empty'>{{ description }}</div>" },
}));

const props = { box: "build-box", token: "t", directory: "/srv/app" };

describe("ContentSearchInput", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("a failed search shows its message where the empty result would show", async () => {
    vi.mocked(grepContent).mockRejectedValue(new ApiError(0, "network", ""));
    const { getByTestId, getAllByTestId } = render(ContentSearchInput, { props });
    await fireEvent.update(getByTestId("search-input"), "TODO");
    await vi.advanceTimersByTimeAsync(500);

    await waitFor(() =>
      expect(getAllByTestId("empty").map((el) => el.textContent)).toEqual([
        "No answer from sshler; check the connection",
      ]),
    );
  });

  it("a search the user replaced by typing on shows nothing of its failure", async () => {
    let failFirst: (err: unknown) => void = () => {};
    vi.mocked(grepContent)
      .mockReturnValueOnce(new Promise((_, reject) => { failFirst = reject; }))
      .mockResolvedValueOnce({ box: "build-box", pattern: "TODOS", directory: "/srv/app", matches: [{ file: "/srv/app/a.py", line_number: 3, line: "# TODOS" }], truncated: false });
    const { getByTestId, getByText, queryAllByTestId } = render(ContentSearchInput, { props });
    await fireEvent.update(getByTestId("search-input"), "TODO");
    await vi.advanceTimersByTimeAsync(500);
    await fireEvent.update(getByTestId("search-input"), "TODOS");
    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(getByText("/srv/app/a.py")).toBeTruthy());

    failFirst(new ApiError(502, "unreachable", "late failure"));
    await vi.advanceTimersByTimeAsync(0);

    expect(getByText("/srv/app/a.py")).toBeTruthy();
    expect(queryAllByTestId("empty")).toHaveLength(0);
  });
});
