/**
 * uploadFile: the overwrite flag, and a refused upload's failure, which is the same
 * ApiError as every request's. A fake XMLHttpRequest records the form and answers with
 * a chosen status and body.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./apiError";
import { uploadFile } from "./http";

type Answer = { status: number; body: string } | "network-error";

const pushMock = vi.fn(async (_to: unknown) => {});
vi.mock("@/router", () => ({
  default: {
    currentRoute: { value: { name: "files", fullPath: "/files?box=b" } },
    push: (to: unknown) => pushMock(to),
    isReady: async () => {},
  },
}));
vi.mock("@/stores/auth", () => ({ useAuthStore: () => ({ clearUser: vi.fn() }) }));

const bootstrapState = { token: null as string | null };
const bootstrapMock = vi.fn(async () => {
  bootstrapState.token = "fresh-token";
});
vi.mock("@/stores/bootstrap", () => ({
  useBootstrapStore: () => ({
    setToken: (v: string | null) => { bootstrapState.token = v; },
    bootstrap: bootstrapMock,
    forget: () => {},
    get token() { return bootstrapState.token; },
  }),
}));

class FakeXHR {
  static answer: Answer = { status: 200, body: "{}" };
  /** When set, answers in turn, one per request, before `answer`. */
  static answers: Answer[] = [];
  static sent: FormData[] = [];
  static urls: string[] = [];
  static tokens: (string | null)[] = [];
  token: string | null = null;
  status = 0;
  responseText = "";
  withCredentials = false;
  upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  open(_method: string, url: string) { FakeXHR.urls.push(url); }
  setRequestHeader(key: string, value: string) { if (key === "X-SSHLER-TOKEN") this.token = value; }
  send(form: FormData) {
    FakeXHR.sent.push(form);
    FakeXHR.tokens.push(this.token);
    const answer = FakeXHR.answers.shift() ?? FakeXHR.answer;
    queueMicrotask(() => {
      if (answer === "network-error") {
        this.onerror?.();
        return;
      }
      this.status = answer.status;
      this.responseText = answer.body;
      void this.onload?.();
    });
  }
}

describe("uploadFile", () => {
  beforeEach(() => {
    FakeXHR.sent = [];
    FakeXHR.urls = [];
    FakeXHR.tokens = [];
    FakeXHR.answers = [];
    bootstrapState.token = null;
    bootstrapMock.mockClear();
    pushMock.mockClear();
    FakeXHR.answer = { status: 200, body: JSON.stringify({ status: "ok", message: "uploaded", path: "/srv/a.txt" }) };
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // Mutation: drop the `overwrite` field (the re-send after "Replace" is refused again)
  // or always send it (every upload would replace without asking).
  it("sends overwrite=true only when asked", async () => {
    const file = new File(["x"], "a.txt");
    await uploadFile("box", "/srv", file, "tok");
    await uploadFile("box", "/srv", file, "tok", undefined, { overwrite: true });
    expect(FakeXHR.sent.map((f) => f.get("overwrite"))).toEqual([null, "true"]);
    expect(FakeXHR.sent.map((f) => f.get("directory"))).toEqual(["/srv", "/srv"]);
    expect(FakeXHR.urls).toEqual(["/api/v1/boxes/box/upload", "/api/v1/boxes/box/upload"]);
  });

  const fields = (err: unknown) => {
    if (!(err instanceof ApiError)) throw new Error(`not an ApiError: ${String(err)}`);
    return [err.status, err.reason, err.detail, err.message];
  };
  const refused = () => uploadFile("box", "/srv", new File(["x"], "a.txt"), "tok").catch((e: unknown) => e);

  // Mutation: a failure without its status or reason; the view cannot tell "already
  // exists" from a failure worth retrying.
  it.each([
    [409, { detail: "File already exists", reason: "exists" },
      [409, "exists", "File already exists", "That name is already taken — File already exists"]],
    [409, { detail: "File already exists", reason: "cannot_replace" },
      [409, "cannot_replace", "File already exists", "That name is taken and cannot be replaced — File already exists"]],
    [400, { detail: "Only a regular file can be replaced", reason: "cannot_replace" },
      [400, "cannot_replace", "Only a regular file can be replaced",
        "That name is taken and cannot be replaced — Only a regular file can be replaced"]],
    [413, { detail: "Upload exceeds 1024 KB limit", reason: "too_large" },
      [413, "too_large", "Upload exceeds 1024 KB limit", "Too large — Upload exceeds 1024 KB limit"]],
    [409, { detail: "File already exists" }, [409, null, "File already exists", "The request failed — File already exists"]],
    [502, {}, [502, null, "", "The request failed (502)"]],
  ])("a %i answer %j rejects with its ApiError", async (status, body, expected) => {
    FakeXHR.answer = { status, body: JSON.stringify(body) };
    expect(fields(await refused())).toEqual(expected);
    expect(FakeXHR.sent).toHaveLength(1);
  });

  // Mutation: give a network error a 4xx-looking status; the view would stop retrying it.
  it("a network error rejects with status 0 and the reason network", async () => {
    FakeXHR.answer = "network-error";
    expect(fields(await refused())).toEqual([0, "network", "", "No answer from sshler; check the connection"]);
  });

  // Mutation: the upload does not act on `token`; a server restart fails every upload.
  it("token: refreshes once and sends once more with the new token", async () => {
    FakeXHR.answers = [{ status: 403, body: JSON.stringify({ detail: "bad token", reason: "token" }) }];
    const answer = await uploadFile("box", "/srv", new File(["x"], "a.txt"), "stale-token");
    expect(answer).toEqual({ status: "ok", message: "uploaded", path: "/srv/a.txt" });
    expect(FakeXHR.tokens).toEqual(["stale-token", "fresh-token"]);
    expect(bootstrapMock).toHaveBeenCalledTimes(1);
  });

  it("denied: rejected at once, one request, no refresh", async () => {
    FakeXHR.answer = { status: 403, body: JSON.stringify({ detail: "Permission denied", reason: "denied" }) };
    expect(fields(await refused())).toEqual([403, "denied", "Permission denied", "Permission denied — Permission denied"]);
    expect(FakeXHR.sent).toHaveLength(1);
    expect(bootstrapMock).toHaveBeenCalledTimes(0);
  });

  it("auth: rejected with its ApiError, one request, one navigation to the sign-in page", async () => {
    FakeXHR.answer = { status: 401, body: JSON.stringify({ detail: "Not authenticated", reason: "auth" }) };
    expect(fields(await refused())).toEqual([401, "auth", "Not authenticated", "You need to sign in — Not authenticated"]);
    expect(FakeXHR.sent).toHaveLength(1);
    expect(pushMock.mock.calls).toEqual([[{ name: "login", query: { redirect: "/files?box=b" } }]]);
  });

  it("token twice: two requests, one refresh, rejected with the second failure", async () => {
    FakeXHR.answers = [
      { status: 403, body: JSON.stringify({ detail: "bad token", reason: "token" }) },
      { status: 403, body: JSON.stringify({ detail: "still bad", reason: "token" }) },
    ];
    expect(fields(await refused())).toEqual([
      403, "token", "still bad", "This page lost its access to sshler; reload the page — still bad",
    ]);
    expect(FakeXHR.tokens).toEqual(["tok", "fresh-token"]);
    expect(bootstrapMock).toHaveBeenCalledTimes(1);
  });
});
