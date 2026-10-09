import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildHeaders as _buildHeaders,
  downloadDirectory,
  fetchBoxes,
  filenameFromContentDisposition,
} from "./http";

// Token refresh goes through the bootstrap store; mock it so the spec controls
// which token a refresh yields and counts how often a refresh happens.
const bootstrapState = { token: null as string | null, nextToken: "fresh-token" as string | null };
const bootstrapMock = vi.fn(async () => {
  bootstrapState.token = bootstrapState.nextToken;
});
const setTokenMock = vi.fn((v: string | null) => {
  bootstrapState.token = v;
});
vi.mock("@/stores/bootstrap", () => ({
  useBootstrapStore: () => ({
    setToken: setTokenMock,
    bootstrap: bootstrapMock,
    get token() {
      return bootstrapState.token;
    },
  }),
}));

const clearUserMock = vi.fn();
vi.mock("@/stores/auth", () => ({
  useAuthStore: () => ({ clearUser: clearUserMock }),
}));

/** Response with a settable `url`, as a real fetch Response would carry. */
function res(status: number, body: unknown, url = "/api/v1/boxes"): Response {
  const r = new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
  Object.defineProperty(r, "url", { value: url });
  return r;
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  bootstrapMock.mockClear();
  setTokenMock.mockClear();
  clearUserMock.mockClear();
  bootstrapState.token = null;
  bootstrapState.nextToken = "fresh-token";
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("http helpers", () => {
  it("buildHeaders adds token when provided", () => {
    const headers = _buildHeaders("token123");
    expect((headers as Record<string, string>)["X-SSHLER-TOKEN"]).toBe("token123");
  });
});

describe("apiFetch 403 token retry", () => {
  // Mutation killed: removing the 403 branch in handle(), or dropping the new
  // token from the replayed request's headers.
  it("refreshes the token once and replays the request once with the new token", async () => {
    fetchMock
      .mockResolvedValueOnce(res(403, { detail: "bad token" }))
      .mockResolvedValueOnce(res(200, [{ name: "box1" }]));

    const boxes = await fetchBoxes("stale-token");

    expect(boxes).toEqual([{ name: "box1" }]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/v1/boxes");
    expect(fetchMock.mock.calls[0]![1].headers["X-SSHLER-TOKEN"]).toBe("stale-token");
    expect(fetchMock.mock.calls[1]![0]).toBe("/api/v1/boxes");
    expect(fetchMock.mock.calls[1]![1].headers["X-SSHLER-TOKEN"]).toBe("fresh-token");
    expect(fetchMock.mock.calls[1]![1].credentials).toBe("include");
    expect(setTokenMock).toHaveBeenCalledWith(null);
    expect(bootstrapMock).toHaveBeenCalledTimes(1);
  });

  // Mutation killed: replaying through apiFetch/handle recursively (retry loop).
  it("does not loop when the replay also returns 403; throws the original detail", async () => {
    fetchMock
      .mockResolvedValueOnce(res(403, { detail: "bad token" }))
      .mockResolvedValueOnce(res(403, { detail: "still bad" }));

    await expect(fetchBoxes("stale-token")).rejects.toThrow(new Error("bad token"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(bootstrapMock).toHaveBeenCalledTimes(1);
  });

  // Mutation killed: dropping the `if (newToken)` guard (replay with no token).
  it("does not replay when the refresh yields no token", async () => {
    bootstrapState.nextToken = null;
    fetchMock.mockResolvedValueOnce(res(403, { detail: "bad token" }));

    await expect(fetchBoxes("stale-token")).rejects.toThrow(new Error("bad token"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // Mutation killed: dropping the `!response.url.includes('/bootstrap')` guard.
  it("never refreshes on a 403 from /bootstrap itself", async () => {
    fetchMock.mockResolvedValueOnce(res(403, { detail: "nope" }, "/api/v1/bootstrap?_t=1"));
    const { fetchBootstrap } = await import("./http");

    await expect(fetchBootstrap()).rejects.toThrow(new Error("nope"));
    expect(bootstrapMock).toHaveBeenCalledTimes(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("apiFetch error mapping", () => {
  // Mutation killed: ignoring `detail` in safeParseError.
  it("uses a string detail as the error message", async () => {
    fetchMock.mockResolvedValueOnce(res(404, { detail: "Box not found" }));
    await expect(fetchBoxes("t")).rejects.toThrow(new Error("Box not found"));
  });

  // Mutation killed: not joining list-shaped (FastAPI 422) details by ", ".
  it("joins a list detail's msg fields", async () => {
    fetchMock.mockResolvedValueOnce(
      res(422, { detail: [{ msg: "field required" }, { msg: "bad path" }] }),
    );
    await expect(fetchBoxes("t")).rejects.toThrow(new Error("field required, bad path"));
  });

  // Mutation killed: changing the fallback message format.
  it("falls back to the status when the body has no detail", async () => {
    fetchMock.mockResolvedValueOnce(res(500, { oops: true }));
    await expect(fetchBoxes("t")).rejects.toThrow(new Error("request failed with 500"));
  });

  // Mutation killed: removing the 401 handleAuthErrors call.
  it("clears the auth store and redirects to login on 401", async () => {
    window.history.pushState({}, "", "/app/files");
    fetchMock.mockResolvedValueOnce(res(401, { detail: "Not authenticated" }));
    const hrefSet = vi.fn();
    const original = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        pathname: "/app/files",
        set href(v: string) {
          hrefSet(v);
        },
      },
    });
    try {
      await expect(fetchBoxes("t")).rejects.toThrow(new Error("Not authenticated"));
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: original });
    }
    expect(clearUserMock).toHaveBeenCalledTimes(1);
    expect(hrefSet).toHaveBeenCalledWith("/login?redirect=%2Fapp%2Ffiles");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

// Header values below are exactly what sshler/api/files.py `_attachment_header`
// emits for these names (printed from the Python function, not re-derived here).
describe("filenameFromContentDisposition", () => {
  // Mutation killed: reading `filename=` before `filename*=` returns the ASCII
  // fallback ("___.zip", "my _q_ 100_.zip") instead of the real name.
  it.each([
    [`attachment; filename="___.zip"; filename*=UTF-8''%E6%97%A5%E6%9C%AC%E8%AA%9E.zip`, "日本語.zip"],
    [`attachment; filename="my _q_ 100_.zip"; filename*=UTF-8''my%20%22q%22%20100%25.zip`, 'my "q" 100%.zip'],
    [`attachment; filename="u.zip"; filename*=UTF-8''u.zip`, "u.zip"],
  ])("prefers the RFC 5987 filename*: %s", (header, expected) => {
    expect(filenameFromContentDisposition(header)).toBe(expected);
  });

  // Mutation killed: dropping the plain-form fallback returns null for these.
  it.each([
    [`attachment; filename="plain.zip"`, "plain.zip"],
    [`attachment; filename=bare.zip`, "bare.zip"],
    [`attachment; filename="fallback.zip"; filename*=UTF-8''%E6%97%A5%ZZ.zip`, "fallback.zip"],
  ])("falls back to the plain filename: %s", (header, expected) => {
    expect(filenameFromContentDisposition(header)).toBe(expected);
  });

  it.each([[null], ["attachment"], ["inline"], [`attachment; filename=""`]])(
    "is null when the header names no file: %j",
    (header) => {
      expect(filenameFromContentDisposition(header)).toBeNull();
    },
  );
});

describe("downloadDirectory", () => {
  // Mutation killed: returning only the Blob (the pre-fix contract) or ignoring
  // the response header leaves `filename` undefined/null and the caller falls
  // back to "home.zip" at `~`.
  it("returns the zip and the server's file name from Content-Disposition", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("PK-zip-bytes", {
        status: 200,
        headers: {
          "Content-Type": "application/zip",
          "Content-Disposition": `attachment; filename="gabu.zip"; filename*=UTF-8''gabu.zip`,
        },
      }),
    );
    const out = await downloadDirectory("devbox", "~", "tok");
    expect(out.filename).toBe("gabu.zip");
    expect(out.blob.size).toBe("PK-zip-bytes".length);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/v1/boxes/devbox/download-dir?path=%7E");
  });
});
