import { afterEach, describe, expect, it, vi } from "vitest";

import { filesUrl, normalizeDir, openInBackgroundTab, terminalUrl } from "./dirLinks";

describe("dirLinks", () => {
  afterEach(() => vi.restoreAllMocks());

  it("percent-encodes box and folder in the terminal link", () => {
    expect(terminalUrl("local", "/h/my dir 100%#x")).toBe(
      "/app/terminal?box=local&dir=%2Fh%2Fmy%20dir%20100%25%23x",
    );
  });

  it("percent-encodes box and folder in the files link", () => {
    expect(filesUrl("my box", "/h/a b")).toBe("/app/files?box=my%20box&path=%2Fh%2Fa%20b");
  });

  it("normalises Windows folders to forward slashes", () => {
    expect(normalizeDir("C:\\Users\\x\\repo")).toBe("C:/Users/x/repo");
    expect(normalizeDir("C:/Users/x/repo")).toBe("C:/Users/x/repo");
  });

  it("leaves a POSIX backslash in a name alone", () => {
    expect(normalizeDir("/h/a\\b")).toBe("/h/a\\b");
  });

  it("opens a background tab, keeps focus and cancels the native click", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const focus = vi.spyOn(window, "focus").mockImplementation(() => {});
    const event = new MouseEvent("click", { cancelable: true });
    openInBackgroundTab(event, "/app/files?box=local&path=%2Fx");
    expect(event.defaultPrevented).toBe(true);
    expect(open).toHaveBeenCalledWith("/app/files?box=local&path=%2Fx", "_blank");
    expect(focus).toHaveBeenCalled();
  });

  it.each([
    ["Ctrl", { ctrlKey: true }],
    ["Meta", { metaKey: true }],
    ["Shift", { shiftKey: true }],
    ["Alt", { altKey: true }],
    ["the middle button", { button: 1 }],
  ])("leaves a click with %s to the browser", (_name, init) => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const event = new MouseEvent("click", { cancelable: true, ...init });
    openInBackgroundTab(event, "/app/files?box=local&path=%2Fx");
    expect([event.defaultPrevented, open.mock.calls.length]).toEqual([false, 0]);
  });
});
