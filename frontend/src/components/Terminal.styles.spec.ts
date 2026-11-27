// The terminal page's files take their motion from main.css: no "transition: all", every
// transition timed by the tokens, no copy of the spin keyframes or of the reduced-motion
// switch (main.css owns both), and the phone breakpoint is 767 px like the rest of the app.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const src = resolve(__dirname, "..");
const FILES = [
  "components/Terminal.vue",
  "views/TerminalView.vue",
  "components/MobileInputBar.vue",
  "components/SessionSwitcher.vue",
];

const styleOf = (file: string) =>
  [...readFileSync(resolve(src, file), "utf8").matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join("\n");

describe("the terminal page's motion", () => {
  it.each(FILES)("%s never transitions all", (file) => {
    expect(styleOf(file).match(/transition:\s*all\b[^;]*/g) ?? []).toEqual([]);
  });

  it.each(FILES)("%s times every transition with the tokens", (file) => {
    const untimed = [...styleOf(file).matchAll(/transition:\s*([^;]+);/g)]
      .map((m) => m[1]!.trim())
      .filter((value) => value !== "none" && !(/var\(--dur-/.test(value) && /var\(--ease-/.test(value)));
    expect(untimed).toEqual([]);
  });

  it.each(FILES)("%s keeps no copy of the spin keyframes or the reduced-motion switch", (file) => {
    const css = styleOf(file);
    expect(css.match(/@keyframes\s+spin\b/g) ?? []).toEqual([]);
    expect(css.match(/prefers-reduced-motion/g) ?? []).toEqual([]);
  });

  it.each(FILES)("%s puts the phone below 768 px", (file) => {
    expect(styleOf(file).match(/(?:max-width:\s*768px|min-width:\s*769px)/g) ?? []).toEqual([]);
  });
});

// The title bar is one row of at most 32 px when its own width is under 700 px, measured by
// a container query on the bar, not by the window.
describe("the narrow title bar", () => {
  const css = styleOf("components/Terminal.vue");

  it("is its own query container, so a pane is judged by its width", () => {
    expect(css).toMatch(/\.terminal-titlebar\s*\{[^}]*container-type:\s*inline-size/);
  });

  it("switches to the one-row layout under 700 px of its own width", () => {
    expect(css).toMatch(/@container\s*\(max-width:\s*699px\)/);
  });

  it("cuts the title with an ellipsis instead of wrapping it", () => {
    const narrow = css.slice(css.indexOf("@container (max-width: 699px)"));
    expect(narrow).toMatch(/text-overflow:\s*ellipsis/);
    expect(narrow).toMatch(/white-space:\s*nowrap/);
  });

  // R4c.r5: the freshness dot is a state, not an action; the one-row bar keeps it.
  it("hides no snapshot dot in the one-row bar", () => {
    const narrow = css.slice(css.indexOf("@container (max-width: 699px)"));
    const hide = narrow.match(/([^{}]*)\{\s*display:\s*none;?\s*\}/g) ?? [];
    expect(hide.join("\n")).not.toContain("snapshot-indicator");
  });
});
