import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import type { ApiSession } from "@/api/types";
import { useSessionsStore } from "./sessions";

vi.mock("@/api/http", () => ({
  fetchBoxSessions: vi.fn(),
  syncBoxSessions: vi.fn(),
}));

import { fetchBoxSessions } from "@/api/http";

const session: ApiSession = {
  id: "s1", box: "build-box", session_name: "api", working_directory: "/srv",
  created_at: 0, last_accessed_at: 0, active: true, window_count: 1, metadata: {},
};

describe("the sessions store: loading a box's sessions", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("a box that could not be asked keeps the sessions it had and puts the failure's message in error", async () => {
    vi.mocked(fetchBoxSessions)
      .mockResolvedValueOnce([session])
      .mockRejectedValueOnce(new ApiError(502, "unreachable", "build-box did not answer"));
    const store = useSessionsStore();
    await store.loadAllBoxSessions(["build-box"], "t");

    await store.loadAllBoxSessions(["build-box"], "t");

    expect(store.getBoxSessions("build-box")).toEqual([session]);
    expect(store.error).toBe("Cannot connect to the box — build-box did not answer");
  });
});
