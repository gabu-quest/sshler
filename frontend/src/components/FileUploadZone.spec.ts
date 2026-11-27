import { describe, expect, it } from "vitest";
import source from "./FileUploadZone.vue?raw";

// jsdom does not compute scoped CSS, so the rules are read from the source.
function rule(selector: string): string {
  const start = source.indexOf(`${selector} {`);
  expect(start, `rule ${selector} exists`).toBeGreaterThan(-1);
  return source.slice(start, source.indexOf("}", start));
}

describe("FileUploadZone: dragging a file over the table", () => {
  // Mutation: `transition: all` plus a 2px border added on drag-over moves the table.
  it("does not animate everything", () => {
    expect(rule(".file-browser")).not.toMatch(/transition:\s*all/);
  });

  // The frame is an outline drawn inside the edge: it takes no space, so the table is not inset at rest
  // and does not move on drag-over (a border, even a transparent one, insets the table by its width).
  it("draws the drag frame as an inside outline, never a border", () => {
    expect(rule(".file-browser")).not.toMatch(/border:/);
    expect(rule(".file-browser")).toMatch(/outline:\s*2px dashed transparent/);
    expect(rule(".file-browser")).toMatch(/outline-offset:\s*-2px/);
    expect(rule(".file-browser.drag-over")).not.toMatch(/border/);
    expect(rule(".file-browser.drag-over")).toMatch(/outline-color:\s*var\(--accent\)/);
  });

  it("animates only colour (no layout property)", () => {
    expect(rule(".file-browser")).toMatch(/transition:[^;]*outline-color[^;]*background-color/);
  });
});
