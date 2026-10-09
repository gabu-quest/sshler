/**
 * uploadFile: the overwrite flag and the status on a refused upload (wave 5d, WS-AG).
 * A fake XMLHttpRequest records the form and answers with a chosen status and body.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UploadError, uploadFile } from "./http";

type Answer = { status: number; body: string } | "network-error";

class FakeXHR {
  static answer: Answer = { status: 200, body: "{}" };
  static sent: FormData[] = [];
  static urls: string[] = [];
  status = 0;
  responseText = "";
  withCredentials = false;
  upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  open(_method: string, url: string) { FakeXHR.urls.push(url); }
  setRequestHeader() {}
  send(form: FormData) {
    FakeXHR.sent.push(form);
    const answer = FakeXHR.answer;
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

  // Mutation: reject with a plain Error (the pre-5d client); the caller cannot tell
  // "already exists" from a failure worth retrying, and `status` is undefined.
  it.each([
    [409, { detail: "File already exists" }, "File already exists"],
    [400, { detail: "Upload exceeds 1024 KB limit" }, "Upload exceeds 1024 KB limit"],
    [502, {}, "upload failed with 502"],
  ])("a %i answer rejects with an UploadError carrying that status", async (status, body, msg) => {
    FakeXHR.answer = { status, body: JSON.stringify(body) };
    const err = await uploadFile("box", "/srv", new File(["x"], "a.txt"), "tok").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(UploadError);
    expect([(err as UploadError).status, (err as UploadError).message]).toEqual([status, msg]);
  });

  // Mutation: give a network error a 4xx-looking status; the view would stop retrying it.
  it("a network error rejects with status 0", async () => {
    FakeXHR.answer = "network-error";
    const err = await uploadFile("box", "/srv", new File(["x"], "a.txt"), "tok").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(UploadError);
    expect([(err as UploadError).status, (err as UploadError).message]).toEqual([0, "upload failed: network error"]);
  });
});
