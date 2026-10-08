import { describe, expect, it } from "vitest";

import { buildArtifactUrl, isLoopbackHostname } from "./artifactUrl";

describe("artifact URL helpers", () => {
  it("recognizes only loopback hostnames", () => {
    expect(isLoopbackHostname("localhost")).toBe(true);
    expect(isLoopbackHostname("127.0.0.1")).toBe(true);
    expect(isLoopbackHostname("[::1]")).toBe(true);
    expect(isLoopbackHostname("workstation.example")).toBe(false);
  });

  it("uses a separate loopback origin for local HTML", () => {
    expect(buildArtifactUrl("/a/item/index.html", 41000, "localhost")).toBe(
      "http://127.0.0.1:41000/a/item/index.html",
    );
    expect(buildArtifactUrl("a/item/index.html", 41000, "127.0.0.1")).toBe(
      "http://127.0.0.1:41000/a/item/index.html",
    );
  });

  it("disables opening from non-loopback UIs or without a sidecar port", () => {
    expect(buildArtifactUrl("/a/item/index.html", 41000, "workstation.example")).toBeNull();
    expect(buildArtifactUrl("/a/item/index.html", null, "localhost")).toBeNull();
  });
});
