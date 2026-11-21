import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  batchCopy,
  batchMove,
  buildHeaders as _buildHeaders,
  crossBoxTransfer,
  downloadDirectory,
  downloadFile,
  exportPdf,
  extractArchive,
  fetchBootstrap,
  fetchBoxes,
  filenameFromContentDisposition,
  http,
  renameFile,
  sameBoxTransfer,
  signInAfterSocketClose,
  tokenWorks,
} from "./http";
import { ApiError } from "./apiError";
import { createI18n } from "@/i18n";
import type { App } from "vue";

// The real translation, so the Japanese line is the one a person reads.
vi.unmock("@/i18n");

// Token refresh goes through the bootstrap store; mock it so the spec controls
// which token a refresh yields and counts how often a refresh happens.
const bootstrapState = { token: null as string | null, nextToken: "fresh-token" as string | null };
const bootstrapMock = vi.fn(async () => {
  bootstrapState.token = bootstrapState.nextToken;
});
const setTokenMock = vi.fn((v: string | null) => {
  bootstrapState.token = v;
});
const forgetMock = vi.fn();
vi.mock("@/stores/bootstrap", () => ({
  useBootstrapStore: () => ({
    setToken: setTokenMock,
    forget: forgetMock,
    bootstrap: bootstrapMock,
    get token() {
      return bootstrapState.token;
    },
  }),
}));

// The router the sign-in redirect goes through: the spec sets the current route and
// reads every navigation.
const routerState = { current: { name: "files", fullPath: "/files?box=b" } as { name: string; fullPath: string } };
const readyState = { promise: Promise.resolve() as Promise<void> };
const pushMock = vi.fn(async (_to: unknown) => {});
vi.mock("@/router", () => ({
  default: {
    get currentRoute() { return { value: routerState.current }; },
    push: (to: unknown) => pushMock(to),
    isReady: () => readyState.promise,
  },
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
  forgetMock.mockClear();
  pushMock.mockClear();
  readyState.promise = Promise.resolve();
  routerState.current = { name: "files", fullPath: "/files?box=b" };
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

/** The fields a person and a reader see of a thrown failure. */
async function failure(promise: Promise<unknown>): Promise<[number, string | null, string, string]> {
  const err = await promise.then(
    () => { throw new Error("the request did not fail"); },
    (e: unknown) => e,
  );
  if (!(err instanceof ApiError)) throw new Error(`not an ApiError: ${String(err)}`);
  return [err.status, err.reason, err.detail, err.message];
}

function setLocale(value: "en" | "ja") {
  localStorage.setItem("sshler:locale", value);
  createI18n({ provide() {} } as unknown as App);
}

describe("the one request: what a failure does and shows", () => {
  afterEach(() => setLocale("en"));

  it("token: refreshes once, sends once more with the new token, and answers", async () => {
    fetchMock
      .mockResolvedValueOnce(res(403, { detail: "bad token", reason: "token" }))
      .mockResolvedValueOnce(res(200, [{ name: "box1" }]));

    expect(await fetchBoxes("stale-token")).toEqual([{ name: "box1" }]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/v1/boxes");
    expect(fetchMock.mock.calls[0]![1].headers["X-SSHLER-TOKEN"]).toBe("stale-token");
    expect(fetchMock.mock.calls[1]![0]).toBe("/api/v1/boxes");
    expect(fetchMock.mock.calls[1]![1].headers["X-SSHLER-TOKEN"]).toBe("fresh-token");
    expect(fetchMock.mock.calls[1]![1].credentials).toBe("include");
    expect(setTokenMock).toHaveBeenCalledWith(null);
    expect(bootstrapMock).toHaveBeenCalledTimes(1);
  });

  it("token twice: the second failure is thrown and no third request is sent", async () => {
    fetchMock
      .mockResolvedValueOnce(res(403, { detail: "bad token", reason: "token" }))
      .mockResolvedValueOnce(res(403, { detail: "still bad", reason: "token" }));

    expect(await failure(fetchBoxes("stale-token"))).toEqual([
      403, "token", "still bad", "This page lost its access to sshler; reload the page — still bad",
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(bootstrapMock).toHaveBeenCalledTimes(1);
  });

  it("token, when the refresh yields no token: the first failure is thrown, nothing is sent again", async () => {
    bootstrapState.nextToken = null;
    fetchMock.mockResolvedValueOnce(res(403, { detail: "bad token", reason: "token" }));

    expect(await failure(fetchBoxes("stale-token"))).toEqual([
      403, "token", "bad token", "This page lost its access to sshler; reload the page — bad token",
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("token, when the refresh itself fails: the first failure is thrown", async () => {
    bootstrapMock.mockRejectedValueOnce(new Error("bootstrap down"));
    fetchMock.mockResolvedValueOnce(res(403, { detail: "bad token", reason: "token" }));

    expect(await failure(fetchBoxes("stale-token"))).toEqual([
      403, "token", "bad token", "This page lost its access to sshler; reload the page — bad token",
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("token on two requests at once: one refresh, each sent once more", async () => {
    fetchMock
      .mockResolvedValueOnce(res(403, { detail: "bad token", reason: "token" }))
      .mockResolvedValueOnce(res(403, { detail: "bad token", reason: "token" }))
      .mockResolvedValueOnce(res(200, [{ name: "a" }]))
      .mockResolvedValueOnce(res(200, [{ name: "b" }]));

    const answers = await Promise.all([fetchBoxes("stale"), fetchBoxes("stale")]);
    expect(answers).toEqual([[{ name: "a" }], [{ name: "b" }]]);
    expect(bootstrapMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("token on the bootstrap call itself: thrown, no refresh, one request", async () => {
    fetchMock.mockResolvedValueOnce(res(403, { detail: "nope", reason: "token" }, "/api/v1/bootstrap?_t=1"));

    expect(await failure(fetchBootstrap())).toEqual([
      403, "token", "nope", "This page lost its access to sshler; reload the page — nope",
    ]);
    expect(bootstrapMock).toHaveBeenCalledTimes(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  const signInNavigation = { name: "login", query: { redirect: "/files?box=b" } };

  it("auth: clears the signed-in user, goes to the sign-in page, and throws", async () => {
    fetchMock.mockResolvedValueOnce(res(401, { detail: "Not authenticated", reason: "auth" }));

    expect(await failure(fetchBoxes("t"))).toEqual([
      401, "auth", "Not authenticated", "You need to sign in — Not authenticated",
    ]);
    expect(pushMock.mock.calls).toEqual([[signInNavigation]]);
    expect(clearUserMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("auth: the bootstrap forgets who the page was, so Back to a view is not let through", async () => {
    fetchMock.mockResolvedValueOnce(res(401, { detail: "Not authenticated", reason: "auth" }));

    await failure(fetchBoxes("t"));
    expect(forgetMock).toHaveBeenCalledTimes(1);
  });

  it("auth while the sign-in page is shown: no navigation, and it throws", async () => {
    routerState.current = { name: "login", fullPath: "/login?redirect=%2Ffiles" };
    fetchMock.mockResolvedValueOnce(res(401, { detail: "Invalid username or password", reason: "auth" }));

    expect(await failure(http.post("/api/v1/auth/login", { username: "u", password: "p" }))).toEqual([
      401, "auth", "Invalid username or password", "You need to sign in — Invalid username or password",
    ]);
    expect(pushMock).toHaveBeenCalledTimes(0);
  });

  it("auth that arrives before the router is ready: the route is read once it is, and the sign-in page gets no push", async () => {
    routerState.current = { name: "", fullPath: "/" } as { name: string; fullPath: string };
    let ready!: () => void;
    readyState.promise = new Promise<void>((resolve) => { ready = resolve; });
    fetchMock.mockResolvedValueOnce(res(401, { detail: "Not authenticated", reason: "auth" }));

    const failed = failure(fetchBoxes("t"));
    await new Promise((resolve) => setTimeout(resolve, 20));
    routerState.current = { name: "login", fullPath: "/login?redirect=%2Fsettings" };
    ready();
    await failed;
    expect(pushMock).toHaveBeenCalledTimes(0);
  });

  it("auth on two requests at once: one navigation", async () => {
    pushMock.mockImplementationOnce(async () => {
      await Promise.resolve();
      routerState.current = { name: "login", fullPath: "/login?redirect=%2Ffiles%3Fbox%3Db" };
    });
    fetchMock
      .mockResolvedValueOnce(res(401, { detail: "Not authenticated", reason: "auth" }))
      .mockResolvedValueOnce(res(401, { detail: "Not authenticated", reason: "auth" }));

    const both = await Promise.all([failure(fetchBoxes("t")), failure(fetchBoxes("t"))]);
    expect(both.map((f) => f[1])).toEqual(["auth", "auth"]);
    expect(pushMock.mock.calls).toEqual([[signInNavigation]]);
  });

  it("a 401 from a proxy with no reason: thrown, no sign-in redirect", async () => {
    fetchMock.mockResolvedValueOnce(res(401, { error: "proxy auth" }));

    expect(await failure(fetchBoxes("t"))).toEqual([401, null, "", "The request failed (401)"]);
    expect(pushMock).toHaveBeenCalledTimes(0);
    expect(clearUserMock).toHaveBeenCalledTimes(0);
  });

  it("denied: thrown at once, exactly one request, no refresh", async () => {
    fetchMock.mockResolvedValueOnce(res(403, { detail: "Permission denied: /root/x", reason: "denied" }));

    expect(await failure(fetchBoxes("t"))).toEqual([
      403, "denied", "Permission denied: /root/x", "Permission denied — Permission denied: /root/x",
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(bootstrapMock).toHaveBeenCalledTimes(0);
  });

  it("a 403 with no body: thrown at once, no refresh", async () => {
    const empty = new Response(null, { status: 403 });
    fetchMock.mockResolvedValueOnce(empty);

    expect(await failure(fetchBoxes("t"))).toEqual([403, null, "", "The request failed (403)"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(bootstrapMock).toHaveBeenCalledTimes(0);
  });

  it.each([
    ["exists", 409, { detail: "File already exists", reason: "exists" },
      [409, "exists", "File already exists", "That name is already taken — File already exists"]],
    ["not_found", 404, { detail: "File not found at ref main", reason: "not_found" },
      [404, "not_found", "File not found at ref main", "Not found — File not found at ref main"]],
    ["unreachable", 502, { detail: "build-box: connection timed out after 10 s", reason: "unreachable" },
      [502, "unreachable", "build-box: connection timed out after 10 s",
        "Cannot connect to the box — build-box: connection timed out after 10 s"]],
    ["a body with no reason", 500, { detail: "boom" }, [500, null, "boom", "The request failed — boom"]],
    ["a list detail", 422, { detail: [{ msg: "field required" }, { msg: "bad path" }], reason: "invalid" },
      [422, "invalid", "field required, bad path", "The request was not valid — field required, bad path"]],
  ] as const)("%s: thrown with its status, reason, detail and line", async (_label, status, body, expected) => {
    fetchMock.mockResolvedValueOnce(res(status, body));
    expect(await failure(fetchBoxes("t"))).toEqual(expected);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("unreachable, in Japanese: the line is the Japanese one, then the detail", async () => {
    setLocale("ja");
    fetchMock.mockResolvedValueOnce(res(502, { detail: "build-box: connection timed out after 10 s", reason: "unreachable" }));
    expect(await failure(fetchBoxes("t"))).toEqual([
      502, "unreachable", "build-box: connection timed out after 10 s",
      "ボックスに接続できません — build-box: connection timed out after 10 s",
    ]);
  });

  it("a body that is not JSON: no reason, no detail, never its markup", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html><h1>502 Bad Gateway</h1></html>", { status: 502 }));
    expect(await failure(fetchBoxes("t"))).toEqual([502, null, "", "The request failed (502)"]);
  });

  it("no answer at all: status 0, reason network", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    expect(await failure(fetchBoxes("t"))).toEqual([
      0, "network", "", "No answer from sshler; check the connection",
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("an aborted request is still its own AbortError", async () => {
    const abort = new DOMException("The operation was aborted.", "AbortError");
    fetchMock.mockRejectedValueOnce(abort);
    await expect(fetchBoxes("t")).rejects.toBe(abort);
  });
});

describe("a socket closed with 4401: its session ended, the same way as a 401", () => {
  const signInNavigation = { name: "login", query: { redirect: "/files?box=b" } };

  it("clears the signed-in user, forgets the bootstrap and goes to the sign-in page once", async () => {
    await signInAfterSocketClose(4401);
    expect(pushMock.mock.calls).toEqual([[signInNavigation]]);
    expect(clearUserMock).toHaveBeenCalledTimes(1);
    expect(forgetMock).toHaveBeenCalledTimes(1);
  });

  it("three sockets that close together navigate once", async () => {
    pushMock.mockImplementationOnce(async () => {
      await Promise.resolve();
      routerState.current = { name: "login", fullPath: "/login?redirect=%2Ffiles%3Fbox%3Db" };
    });
    await Promise.all([4401, 4401, 4401].map((code) => signInAfterSocketClose(code)));
    expect(pushMock.mock.calls).toEqual([[signInNavigation]]);
  });

  it("on the sign-in page itself: no navigation", async () => {
    routerState.current = { name: "login", fullPath: "/login?redirect=%2Ffiles" };
    await signInAfterSocketClose(4401);
    expect(pushMock).toHaveBeenCalledTimes(0);
  });

  it.each([1000, 1006, 1011, 4403, 4404])("code %i is not the session's end: null, nothing done", (code) => {
    expect(signInAfterSocketClose(code)).toBeNull();
    expect(pushMock).toHaveBeenCalledTimes(0);
    expect(clearUserMock).toHaveBeenCalledTimes(0);
  });
});

describe("the http object's verbs go through the one request", () => {
  it("send the page's token header and the cookie", async () => {
    bootstrapState.token = "page-token";
    fetchMock.mockResolvedValueOnce(res(200, { username: "u" }));

    expect(await http.get("/api/v1/auth/me")).toEqual({ data: { username: "u" } });
    expect(fetchMock.mock.calls[0]![1].headers).toEqual({ "X-SSHLER-TOKEN": "page-token" });
    expect(fetchMock.mock.calls[0]![1].credentials).toBe("include");
  });

  it("refresh the token once on token and send once more", async () => {
    bootstrapState.token = "stale-token";
    fetchMock
      .mockResolvedValueOnce(res(403, { detail: "bad token", reason: "token" }))
      .mockResolvedValueOnce(res(200, { ok: true }));

    expect(await http.put("/api/v1/pool/config", { idle_timeout: 5 })).toEqual({ data: { ok: true } });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1]![1].headers).toEqual({
      "Content-Type": "application/json", "X-SSHLER-TOKEN": "fresh-token",
    });
    expect(fetchMock.mock.calls[1]![1].body).toBe('{"idle_timeout":5}');
    expect(bootstrapMock).toHaveBeenCalledTimes(1);
  });

  it("throw the same ApiError as every request", async () => {
    fetchMock.mockResolvedValueOnce(res(429, { detail: "Rate limit exceeded. Please try again later.", reason: "rate_limited" }));
    expect(await failure(http.post("/api/v1/auth/login", { username: "u", password: "p" }))).toEqual([
      429, "rate_limited", "Rate limit exceeded. Please try again later.",
      "Too many requests; wait a moment and try again — Rate limit exceeded. Please try again later.",
    ]);
  });
});

describe("the downloads and the PDF export fail as every request does", () => {
  it.each([
    ["a file", () => downloadFile("b", "/root/x", "t")],
    ["a folder", () => downloadDirectory("b", "/root", "t")],
    ["a PDF", () => exportPdf("<p>x</p>", "x.pdf", "t")],
  ])("%s refused as denied", async (_label, call) => {
    fetchMock.mockResolvedValueOnce(res(403, { detail: "Permission denied", reason: "denied" }));
    expect(await failure(call())).toEqual([403, "denied", "Permission denied", "Permission denied — Permission denied"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("tokenWorks", () => {
  it("is false on a token failure, with no refresh and no second request", async () => {
    fetchMock.mockResolvedValueOnce(res(403, { detail: "bad token", reason: "token" }));
    expect(await tokenWorks("cached")).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(bootstrapMock).toHaveBeenCalledTimes(0);
    expect(fetchMock.mock.calls[0]![1].headers["X-SSHLER-TOKEN"]).toBe("cached");
  });

  it("is false on auth, with no sign-in redirect", async () => {
    fetchMock.mockResolvedValueOnce(res(401, { detail: "Not authenticated", reason: "auth" }));
    expect(await tokenWorks("cached")).toBe(false);
    expect(pushMock).toHaveBeenCalledTimes(0);
  });

  it("is true when the server answers", async () => {
    fetchMock.mockResolvedValueOnce(res(200, []));
    expect(await tokenWorks("cached")).toBe(true);
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

describe("a name that is taken", () => {
  const sentBody = (n = 0) => JSON.parse(fetchMock.mock.calls[n]![1].body as string);

  // Mutation killed: dropping the reason, so a view cannot tell a replaceable name from a folder.
  it("an error answer carries the server's reason beside its status", async () => {
    fetchMock.mockResolvedValueOnce(res(409, { detail: "File already exists", reason: "cannot_replace" }));
    expect(await failure(renameFile("b", "/d/a", "c", "t"))).toEqual([
      409, "cannot_replace", "File already exists", "That name is taken and cannot be replaced — File already exists",
    ]);
  });

  // Mutation killed: always sending the flag, or never sending it.
  it("rename sends overwrite only when asked", async () => {
    fetchMock.mockImplementation(async () => res(200, { status: "ok", message: "renamed" }));
    await renameFile("b", "/d/a", "c", "t");
    await renameFile("b", "/d/a", "c", "t", { overwrite: true });
    expect(sentBody(0)).toEqual({ path: "/d/a", new_name: "c" });
    expect(sentBody(1)).toEqual({ path: "/d/a", new_name: "c", overwrite: true });
  });

  // Mutation killed: always sending the flag, or never sending it, on an extract.
  it("extract sends overwrite only when asked", async () => {
    fetchMock.mockImplementation(async () => res(200, { status: "ok", message: "Extracted" }));
    await extractArchive("b", "/d/in.zip", "/d", "t");
    await extractArchive("b", "/d/in.zip", "/d", "t", { overwrite: true });
    expect(sentBody(0)).toEqual({ archive_path: "/d/in.zip", destination: "/d" });
    expect(sentBody(1)).toEqual({ archive_path: "/d/in.zip", destination: "/d", overwrite: true });
  });

  it.each([["move", batchMove, "batch/move"], ["copy", batchCopy, "batch/copy"]] as const)(
    "batch %s sends overwrite only when asked",
    async (_mode, call, route) => {
      fetchMock.mockImplementation(async () => res(200, { status: "ok", succeeded: [], failed: [] }));
      await call("b", ["/s/a"], "/d", "t");
      await call("b", ["/s/a"], "/d", "t", { overwrite: true });
      expect(fetchMock.mock.calls[0]![0]).toContain(route);
      expect(sentBody(0)).toEqual({ paths: ["/s/a"], destination: "/d" });
      expect(sentBody(1)).toEqual({ paths: ["/s/a"], destination: "/d", overwrite: true });
    },
  );

  it("a same-box transfer passes the flag on to the batch call", async () => {
    fetchMock.mockImplementation(async () => res(200, { status: "ok", succeeded: [], failed: [] }));
    await sameBoxTransfer("b", ["/s/a"], "/d", "move", "t", { overwrite: true });
    expect(fetchMock.mock.calls[0]![0]).toContain("batch/move");
    expect(sentBody(0)).toEqual({ paths: ["/s/a"], destination: "/d", overwrite: true });
  });

  it("a cross-box transfer sends overwrite only when asked and hands on each item's reason", async () => {
    const sse = 'event: done\ndata: {"succeeded":[],"failed":[{"path":"/s/a","error":"File already exists","reason":"exists"}]}\n\n';
    fetchMock.mockImplementation(async () => new Response(sse, { status: 200 }));
    const run = (options?: { overwrite?: boolean }) =>
      new Promise<unknown>((resolve) => {
        crossBoxTransfer("x", "y", ["/s/a"], "/d", "copy", "t", () => {}, resolve, () => {}, options);
      });
    const first = await run();
    await run({ overwrite: true });
    expect(sentBody(0)).toEqual({ src_box: "x", dest_box: "y", paths: ["/s/a"], destination: "/d", mode: "copy" });
    expect(sentBody(1)).toEqual({
      src_box: "x", dest_box: "y", paths: ["/s/a"], destination: "/d", mode: "copy", overwrite: true,
    });
    expect(first).toEqual({
      succeeded: [], failed: [{ path: "/s/a", error: "File already exists", reason: "exists" }],
    });
  });

  // Mutation: the "failed" event is dropped, so a refused file is known only at the end.
  it("a cross-box transfer hands on each refused item when its event arrives", async () => {
    const item = { path: "/s/a", error: "File already exists", reason: "exists" };
    const sse =
      `event: failed\ndata: ${JSON.stringify(item)}\n\n` +
      'event: progress\ndata: {"file":"/s/b","bytes_done":1,"bytes_total":1,"index":1,"total":2}\n\n' +
      `event: done\ndata: ${JSON.stringify({ succeeded: ["/s/b"], failed: [item] })}\n\n`;
    fetchMock.mockImplementation(async () => new Response(sse, { status: 200 }));
    const seen: unknown[] = [];
    await new Promise<void>((resolve) => {
      crossBoxTransfer(
        "x", "y", ["/s/a", "/s/b"], "/d", "copy", "t",
        (progress) => seen.push(["progress", progress.file]),
        (result) => { seen.push(["done", result.succeeded]); resolve(); },
        () => {},
        { onItemFailed: (failed) => seen.push(["failed", failed]) },
      );
    });
    expect(seen).toEqual([["failed", item], ["progress", "/s/b"], ["done", ["/s/b"]]]);
    expect(sentBody(0)).toEqual({
      src_box: "x", dest_box: "y", paths: ["/s/a", "/s/b"], destination: "/d", mode: "copy",
    });
  });

  describe("a cross-box transfer's event stream", () => {
    const item = { path: "/s/a", error: "File already exists", reason: "exists" };
    const stream =
      `event: failed\ndata: ${JSON.stringify(item)}\n\n` +
      'event: progress\ndata: {"file":"/s/b","bytes_done":1,"bytes_total":1,"index":1,"total":2}\n\n' +
      `event: done\ndata: ${JSON.stringify({ succeeded: ["/s/b"], failed: [item] })}\n\n`;

    /** Serve ``text`` cut into the given pieces and record every call, in order. */
    async function readCut(pieces: Uint8Array[], expectCalls: number) {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          for (const piece of pieces) controller.enqueue(piece);
          controller.close();
        },
      });
      fetchMock.mockImplementation(async () => new Response(body, { status: 200 }));
      const calls: unknown[] = [];
      crossBoxTransfer(
        "x", "y", ["/s/a", "/s/b"], "/d", "copy", "t",
        (progress) => calls.push(["progress", progress.file]),
        (result) => calls.push(["done", result.succeeded]),
        (error) => calls.push(["error", error]),
        { onItemFailed: (failed) => calls.push(["failed", failed]) },
      );
      await vi.waitFor(() => expect(calls.length).toBe(expectCalls));
      return calls;
    }
    const bytes = (text: string) => new TextEncoder().encode(text);
    const uncut = [["failed", item], ["progress", "/s/b"], ["done", ["/s/b"]]];

    it("gives the same calls, in the same order, when it is cut after every single byte", async () => {
      const all = bytes(stream);
      const pieces = Array.from(all, (_b, i) => all.slice(i, i + 1));
      expect(await readCut(pieces, 3)).toEqual(uncut);
    });

    it("gives the same calls when an event: line and its data: line arrive in two reads", async () => {
      const cut = stream.indexOf("data: ", stream.indexOf("event: done"));
      const pieces = [bytes(stream.slice(0, cut)), bytes(stream.slice(cut))];
      expect(await readCut(pieces, 3)).toEqual(uncut);
    });

    it("calls onError once, with a plain text, when it ends with neither done nor error", async () => {
      const cut = stream.indexOf("event: done");
      expect(await readCut([bytes(stream.slice(0, cut))], 3)).toEqual([
        ["failed", item],
        ["progress", "/s/b"],
        ["error", "The transfer ended before it finished."],
      ]);
    });

    it("does not call onError after an error event, and shows its reason's line and detail", async () => {
      const text = 'event: error\ndata: {"detail":"Transfer error: boom","reason":"failed"}\n\n';
      expect(await readCut([bytes(text)], 1)).toEqual([["error", "The operation failed — Transfer error: boom"]]);
    });

    it("a read that breaks in the middle of the stream reaches onError as the no-answer failure", async () => {
      let sent = false;
      const body = new ReadableStream<Uint8Array>({
        pull(controller) {
          if (sent) {
            controller.error(new TypeError("network error"));
            return;
          }
          sent = true;
          controller.enqueue(bytes('event: progress\ndata: {"file":"/s/b","bytes_done":1,"bytes_total":1,"index":1,"total":2}\n\n'));
        },
      });
      fetchMock.mockImplementation(async () => new Response(body, { status: 200 }));
      const calls: unknown[] = [];
      crossBoxTransfer(
        "x", "y", ["/s/b"], "/d", "copy", "t",
        (progress) => calls.push(["progress", progress.file]),
        () => calls.push(["done"]),
        (error) => calls.push(["error", error]),
      );
      await vi.waitFor(() => expect(calls).toHaveLength(2));
      expect(calls).toEqual([["progress", "/s/b"], ["error", "No answer from sshler; check the connection"]]);
    });

    it("the end before done or error reads in Japanese when the page is Japanese", async () => {
      localStorage.setItem("sshler:locale", "ja");
      createI18n({ provide() {} } as unknown as App);
      try {
        const cut = stream.indexOf("event: done");
        expect(await readCut([bytes(stream.slice(0, cut))], 3)).toEqual([
          ["failed", item],
          ["progress", "/s/b"],
          ["error", "転送が終わる前に途切れました。"],
        ]);
      } finally {
        localStorage.setItem("sshler:locale", "en");
        createI18n({ provide() {} } as unknown as App);
      }
    });

    it("hands a refused first answer to onError as the failure's line and detail", async () => {
      fetchMock.mockImplementation(async () => res(502, { detail: "dest: connection refused", reason: "unreachable" }));
      const errors: string[] = [];
      crossBoxTransfer("x", "y", ["/s/a"], "/d", "copy", "t", () => {}, () => {}, (e) => errors.push(e));
      await vi.waitFor(() => expect(errors).toHaveLength(1));
      expect(errors).toEqual(["Cannot connect to the box — dest: connection refused"]);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("a first answer of token: one refresh, one more request, and the second answer's stream is read to done", async () => {
      fetchMock
        .mockResolvedValueOnce(res(403, { detail: "bad token", reason: "token" }))
        .mockResolvedValueOnce(new Response(stream, { status: 200 }));
      const calls: unknown[] = [];
      crossBoxTransfer(
        "x", "y", ["/s/a", "/s/b"], "/d", "copy", "stale-token",
        (progress) => calls.push(["progress", progress.file]),
        (result) => calls.push(["done", result.succeeded]),
        (error) => calls.push(["error", error]),
        { onItemFailed: (failed) => calls.push(["failed", failed]) },
      );
      await vi.waitFor(() => expect(calls).toHaveLength(3));
      expect(calls).toEqual(uncut);
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(fetchMock.mock.calls[1]![1].headers["X-SSHLER-TOKEN"]).toBe("fresh-token");
      expect(bootstrapMock).toHaveBeenCalledTimes(1);
    });
  });
});

describe("a failure's Retry-After", () => {
  function locked(retryAfter: string | null): Response {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (retryAfter !== null) headers["Retry-After"] = retryAfter;
    return new Response(
      JSON.stringify({ detail: "Too many failed authentication attempts. Try again later.", reason: "rate_limited" }),
      { status: 429, headers },
    );
  }

  it.each([
    ["whole seconds", "300", 300],
    ["no header", null, null],
    ["a date, which this server never sends", "Wed, 21 Oct 2026 07:28:00 GMT", null],
  ])("%s", async (_label, header, seconds) => {
    fetchMock.mockResolvedValueOnce(locked(header));
    const err = await http.post("/api/v1/auth/login", { username: "u", password: "p" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).reason).toBe("rate_limited");
    expect((err as ApiError).retryAfter).toBe(seconds);
  });
});
