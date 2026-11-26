// Every variable an owned view or component reads exists in main.css, and their style blocks
// carry no hard-coded colour, font or "transition: all".

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const src = resolve(__dirname, "..");
const mainCss = readFileSync(resolve(__dirname, "main.css"), "utf8");
const defined = new Set([...mainCss.matchAll(/^\s*(--[\w-]+)\s*:/gm)].map((m) => m[1]));

const OWNED = [
  "views/BoxesView.vue",
  "views/SettingsView.vue",
  "views/LoginView.vue",
  "views/NotFoundView.vue",
  "views/ProgressView.vue",
  "views/ClaudeSessionsView.vue",
  "views/ArtifactsView.vue",
  "views/DiffView.vue",
  "components/ContentSearchInput.vue",
  "components/ProgressPicker.vue",
  "components/ShortcutsOverlay.vue",
  "components/CommandPalette.vue",
];

const CSS_COLOURS = [
  "white", "black", "red", "green", "blue", "yellow", "orange", "purple", "pink", "brown", "gray", "grey",
  "silver", "gold", "cyan", "magenta", "lime", "maroon", "navy", "olive", "teal", "aqua", "fuchsia",
  "indigo", "violet", "crimson", "coral", "salmon", "tomato", "khaki", "ivory", "beige", "tan", "azure",
  "lavender", "orchid", "plum", "turquoise", "snow", "linen", "gainsboro", "whitesmoke", "dimgray",
  "darkgray", "lightgray", "slategray", "darkred", "darkgreen", "darkblue", "lightblue", "lightgreen",
];

const read = (file: string) => readFileSync(resolve(src, file), "utf8");
const styleOf = (text: string) => [...text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join("\n");

describe("tokens used by the other views", () => {
  it.each(OWNED)("%s reads only variables that exist", (file) => {
    const text = read(file);
    // Naive UI's own --n-* variables and variables the file sets itself are fine.
    const own = new Set([...text.matchAll(/['"]?(--[\w-]+)['"]?\s*[:=]/g)].map((m) => m[1]));
    const missing = [...text.matchAll(/var\((--[\w-]+)/g)]
      .map((m) => m[1])
      .filter((name) => !name.startsWith("--n-") && !defined.has(name) && !own.has(name));
    expect([...new Set(missing)]).toEqual([]);
  });

  it.each(OWNED)("%s has no hard-coded colour or font in its CSS", (file) => {
    const css = styleOf(read(file));
    expect(css.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []).toEqual([]);
    expect(css.match(/\b(?:rgb|rgba|hsl|hsla)\(/gi) ?? []).toEqual([]);
    const named = new RegExp(`(?<![\\w-])(?:${CSS_COLOURS.join("|")})(?![\\w-])`, "gi");
    // Only look at values, not selectors or property names.
    const values = [...css.matchAll(/:\s*([^;{}]+)[;}]/g)].map((m) => m[1].replace(/var\([^)]*\)/g, "")).join("\n");
    // ArtifactsView frames foreign HTML on white on purpose.
    if (file !== "views/ArtifactsView.vue") expect(values.match(named) ?? []).toEqual([]);
    expect(css.match(/font-family:(?!\s*var\()[^;]+/g) ?? []).toEqual([]);
  });

  it.each(OWNED)("%s never transitions all", (file) => {
    expect(styleOf(read(file))).not.toMatch(/transition:\s*all\b/);
  });

  it.each(OWNED)("%s times transitions with the tokens", (file) => {
    for (const m of styleOf(read(file)).matchAll(/transition:\s*([^;]+);/g)) {
      if (m[1].trim() === "none") continue;
      expect(m[1], m[0]).toMatch(/var\(--dur-/);
      expect(m[1], m[0]).toMatch(/var\(--ease-/);
    }
  });
});
