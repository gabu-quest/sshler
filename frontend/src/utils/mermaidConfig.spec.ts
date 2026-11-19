import { describe, expect, it } from "vitest";
import { mermaidConfig } from "./mermaidConfig";

describe("mermaidConfig", () => {
  it("pins the layout, look and security level for a light theme", () => {
    expect(mermaidConfig("light")).toEqual({
      startOnLoad: false,
      securityLevel: "strict",
      layout: "dagre",
      look: "classic",
      flowchart: { minNodeWidth: 0 },
      state: { minNodeWidth: 0 },
      theme: "default",
    });
  });

  it("uses the dark theme only for 'dark'", () => {
    expect(mermaidConfig("dark").theme).toBe("dark");
    expect(mermaidConfig("anything else").theme).toBe("default");
  });
});
