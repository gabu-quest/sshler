import { describe, expect, it, vi } from "vitest";

import { en } from "@/locales/en";
import { ja } from "@/locales/ja";
import type { App } from "vue";
import { createI18n } from "@/i18n";
import { ApiError, FAILURE_REASONS, apiErrorOf, apiErrorOfBody, errorText, failureText, networkError, reasonKey } from "./apiError";

vi.unmock("@/i18n");

describe("the line of every reason", () => {
  const keys = [...FAILURE_REASONS, "network" as const, null].map(reasonKey);

  it("names seventeen keys: the server's fifteen reasons, network and no reason", () => {
    expect(keys).toHaveLength(17);
    expect(keys.slice(15)).toEqual(["error.reason.network", "error.reason.none"]);
  });

  it("every key has a line in English", () => {
    expect(keys.filter((k) => !en[k])).toEqual([]);
  });

  it("every key has a line in Japanese", () => {
    expect(keys.filter((k) => !ja[k])).toEqual([]);
  });

  it("no line carries a placeholder, a status number or markup", () => {
    const bad = keys.flatMap((k) => [en[k], ja[k]]).filter((line) => /[{}<>]|\d{3}/.test(line ?? ""));
    expect(bad).toEqual([]);
  });
});

describe("ApiError from an answer", () => {
  it("keeps the status, the reason and the detail, and reads as the line then the detail", () => {
    const err = apiErrorOf(502, JSON.stringify({ detail: "build-box: connection timed out after 10 s", reason: "unreachable" }));
    expect(err).toBeInstanceOf(ApiError);
    expect([err.status, err.reason, err.detail, err.message]).toEqual([
      502,
      "unreachable",
      "build-box: connection timed out after 10 s",
      "Cannot connect to the box — build-box: connection timed out after 10 s",
    ]);
  });

  it("joins FastAPI's list of problems by their texts", () => {
    const err = apiErrorOfBody(422, { detail: [{ msg: "field required" }, { msg: "bad path" }], reason: "invalid" });
    expect(err.message).toBe("The request was not valid — field required, bad path");
  });

  it("an empty detail shows the line alone", () => {
    const err = apiErrorOfBody(404, { detail: "", reason: "not_found" });
    expect([err.detail, err.message]).toEqual(["", "Not found"]);
  });

  it("a word the page does not know is no reason, shown with the no-reason line", () => {
    const err = apiErrorOfBody(500, { detail: "boom", reason: "exploded" });
    expect([err.reason, err.message]).toEqual([null, "The request failed — boom"]);
  });

  it("a body that is not JSON gives no reason and no detail, so its markup is never shown", () => {
    const err = apiErrorOf(502, "<html><body>Bad Gateway</body></html>");
    expect([err.status, err.reason, err.detail, err.message]).toEqual([502, null, "", "The request failed (502)"]);
  });

  it("a detail of ten thousand characters is kept whole, as text", () => {
    const long = "<b>".repeat(3334).slice(0, 10_000);
    const err = apiErrorOfBody(500, { detail: long, reason: "failed" });
    expect(err.detail).toBe(long);
    expect(err.message).toBe(`The operation failed — ${long}`);
  });

  it("no reason and no detail: the line, then the status in brackets", () => {
    expect(apiErrorOfBody(502, {}).message).toBe("The request failed (502)");
  });

  it("no reason and no detail, in Japanese: the line, then the status in brackets", () => {
    localStorage.setItem("sshler:locale", "ja");
    createI18n({ provide() {} } as unknown as App);
    try {
      expect(apiErrorOfBody(502, {}).message).toBe("リクエストに失敗しました (502)");
    } finally {
      localStorage.setItem("sshler:locale", "en");
      createI18n({ provide() {} } as unknown as App);
    }
  });

  it("no reason with a detail: the line and the detail, no status", () => {
    expect(apiErrorOfBody(500, { detail: "boom" }).message).toBe("The request failed — boom");
  });

  it("a reason with no detail: the line alone, no status", () => {
    expect(apiErrorOfBody(502, { reason: "unreachable" }).message).toBe("Cannot connect to the box");
  });

  it("no answer at all is status 0 and the reason network", () => {
    const err = networkError();
    expect([err.status, err.reason, err.detail, err.message]).toEqual([
      0, "network", "", "No answer from sshler; check the connection",
    ]);
  });

  // Mutation: the names an extract found taken are dropped, so the page cannot ask about them.
  it("keeps the names a refusal of many names is about", () => {
    const err = apiErrorOfBody(409, {
      detail: "Files of the archive that already exist here (7): a, b and 5 more",
      reason: "exists",
      taken: { count: 7, names: ["a", "b"] },
    });
    expect([err.reason, err.taken]).toEqual(["exists", { count: 7, names: ["a", "b"] }]);
  });

  it.each([
    ["no taken", { detail: "x", reason: "exists" }],
    ["a count that is no number", { detail: "x", reason: "exists", taken: { count: "7", names: ["a"] } }],
    ["a name that is no text", { detail: "x", reason: "exists", taken: { count: 1, names: [1] } }],
  ])("%s gives no names", (_label, body) => {
    expect(apiErrorOfBody(409, body).taken).toBeNull();
  });
});

describe("the text of a failure inside a 200 answer", () => {
  it("reads as the line of the reason, then the text", () => {
    expect(failureText({ error: "build-box did not answer", reason: "unreachable" })).toBe(
      "Cannot connect to the box — build-box did not answer",
    );
  });

  it("is the message an ApiError of the same reason and text has", () => {
    const fields = { error: "git: not found", reason: "program_missing" };
    expect(failureText(fields)).toBe(new ApiError(200, "program_missing", "git: not found").message);
  });

  it("a reason the page does not know reads as the no-reason line and the text, never a key", () => {
    expect(failureText({ error: "boom", reason: "exploded" })).toBe("The request failed — boom");
  });

  it("an empty text leaves the line of the reason alone", () => {
    expect(failureText({ error: "", reason: "timeout" })).toBe("The box took too long to answer");
  });

  it("no reason and no text reads as the no-reason line, with no status", () => {
    expect(failureText({ error: null, reason: null })).toBe("The request failed");
  });
});

describe("what a caught value reads as", () => {
  it.each([
    ["an Error", new Error("boom"), "boom"],
    ["a value that is not an Error", 42, "42"],
  ])("%s", (_name, caught, text) => {
    expect(errorText(caught)).toBe(text);
  });
});
