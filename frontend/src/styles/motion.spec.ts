// The motion tokens, the transition classes, and the reduced-motion switch in main.css,
// and the Naive UI curves that must equal the --ease-* tokens.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { darkThemeOverrides, lightThemeOverrides } from "@/config/naive-theme";

const mainCss = readFileSync(resolve(__dirname, "main.css"), "utf8");

const rootTokens = (): Record<string, string> => {
  const start = mainCss.indexOf(":root {");
  const body = mainCss.slice(start, mainCss.indexOf("\n}", start));
  const tokens: Record<string, string> = {};
  for (const match of body.matchAll(/^\s*(--[\w-]+):\s*([^;]+);/gm)) tokens[match[1]] = match[2].trim();
  return tokens;
};

const blockAfter = (marker: string): string => {
  const start = mainCss.indexOf(marker);
  expect(start, `${marker} is in main.css`).toBeGreaterThan(-1);
  let depth = 0;
  for (let i = mainCss.indexOf("{", start); i < mainCss.length; i++) {
    if (mainCss[i] === "{") depth++;
    if (mainCss[i] === "}" && --depth === 0) return mainCss.slice(start, i + 1);
  }
  return mainCss.slice(start);
};

describe("motion tokens", () => {
  it("has the durations, the curves and the rail and bar sizes", () => {
    const tokens = rootTokens();
    expect(tokens["--dur-instant"]).toBe("80ms");
    expect(tokens["--dur-fast"]).toBe("140ms");
    expect(tokens["--dur-base"]).toBe("220ms");
    expect(tokens["--dur-slow"]).toBe("320ms");
    expect(tokens["--ease-out"]).toBe("cubic-bezier(0.16, 1, 0.3, 1)");
    expect(tokens["--ease-in-out"]).toBe("cubic-bezier(0.4, 0, 0.2, 1)");
    expect(tokens["--ease-in"]).toBe("cubic-bezier(0.4, 0, 1, 1)");
    expect(tokens["--rail-width-collapsed"]).toBe("52px");
    expect(tokens["--rail-width-expanded"]).toBe("208px");
    expect(tokens["--header-height-mobile-nav"]).toBe("36px");
  });

  it.each([
    ["light", lightThemeOverrides],
    ["dark", darkThemeOverrides],
  ])("the %s Naive curves equal the --ease-* tokens", (_name, overrides) => {
    const tokens = rootTokens();
    expect(overrides.common?.cubicBezierEaseOut).toBe(tokens["--ease-out"]);
    expect(overrides.common?.cubicBezierEaseInOut).toBe(tokens["--ease-in-out"]);
    expect(overrides.common?.cubicBezierEaseIn).toBe(tokens["--ease-in"]);
  });

  it("every transition class has its enter and leave rules", () => {
    for (const name of ["route-fade", "route-fade-plain", "menu-pop", "slide-down-sm", "strip-slide", "modal-fade", "list-row", "tab-item"]) {
      expect(mainCss, name).toContain(`.${name}-enter-active`);
      expect(mainCss, name).toContain(`.${name}-leave-active`);
      expect(mainCss, name).toContain(`.${name}-enter-from`);
    }
  });

  it("transition classes move only transform and opacity, from tokens", () => {
    const start = mainCss.indexOf("/* Transition classes");
    expect(start).toBeGreaterThan(-1);
    const section = mainCss.slice(start, mainCss.indexOf("/* Reduced motion"));
    for (const match of section.matchAll(/transition(?:-property)?:\s*([^;]+);/g)) {
      expect(match[1]).not.toMatch(/\ball\b|width|height|margin|top|left/);
    }
    // the stagger step is the one literal time; everything else is a token
    expect(section.replace(/transition-delay:[^;]*;/g, "")).not.toMatch(/\b\d+(\.\d+)?m?s\b(?![^;]*var\()/);
  });

  it("has one spin keyframes", () => {
    expect(mainCss.match(/@keyframes spin\b/g)).toHaveLength(1);
  });
});

describe("reduced motion", () => {
  it("sshler's own class zeroes every duration token and stops transitions", () => {
    const block = blockAfter(":root.reduced-motion {");
    for (const name of ["instant", "fast", "base", "slow"]) expect(block).toContain(`--dur-${name}: 0.01ms`);
    const rule = blockAfter(".reduced-motion *");
    expect(rule).toContain("transition-duration: 0.01ms !important");
    expect(rule).toContain("animation-duration: 0.01ms !important");
  });

  it("the system setting zeroes the same tokens", () => {
    const block = blockAfter("@media (prefers-reduced-motion: reduce)");
    for (const name of ["instant", "fast", "base", "slow"]) expect(block).toContain(`--dur-${name}: 0.01ms`);
    expect(block).toContain("transition-duration: 0.01ms !important");
  });
});
