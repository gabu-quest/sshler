import { describe, expect, it } from "vitest";

import { buildHeaders as _buildHeaders } from "./http";

describe("http helpers", () => {
  it("buildHeaders adds token when provided", () => {
    const headers = _buildHeaders("token123");
    expect((headers as Record<string, string>)["X-SSHLER-TOKEN"]).toBe("token123");
  });
});
