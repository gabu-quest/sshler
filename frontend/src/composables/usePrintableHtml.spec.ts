import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchFilePreview = vi.fn();
vi.mock("@/api/http", () => ({ fetchFilePreview: (...args: unknown[]) => fetchFilePreview(...args) }));

import { usePrintableHtml } from "./usePrintableHtml";

const markdown = (parent: string | undefined) => ({
  content: "![pic](img.png)",
  parent,
});
const image = { image_data: "AAAA", image_mime: "image/png", image_too_large: false };

describe("usePrintableHtml image paths", () => {
  beforeEach(() => fetchFilePreview.mockReset());

  it("resolves a relative image under a drive base without a leading slash", async () => {
    fetchFilePreview.mockResolvedValueOnce(markdown("C:/Users/me")).mockResolvedValueOnce(image);
    const html = await usePrintableHtml().toHtml({ box: "local", path: "C:/Users/me/a.md", token: null });
    expect(fetchFilePreview.mock.calls.map((c) => c[1])).toEqual([
      "C:/Users/me/a.md",
      "C:/Users/me/img.png",
    ]);
    expect(html).toContain("data:image/png;base64,AAAA");
  });

  it("cuts the parent of a drive file itself when the answer names none", async () => {
    fetchFilePreview.mockResolvedValueOnce(markdown(undefined)).mockResolvedValueOnce(image);
    await usePrintableHtml().toHtml({ box: "local", path: "C:\\Users\\a.md", token: null });
    expect(fetchFilePreview.mock.calls.map((c) => c[1])).toEqual(["C:\\Users\\a.md", "C:/Users/img.png"]);
  });
});
