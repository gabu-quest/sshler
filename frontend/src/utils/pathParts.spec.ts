import { describe, expect, it } from "vitest";

import {
  isAbsolutePath,
  isWindowsPath,
  isPathRoot,
  normalizeAbsolute,
  parentPath,
  pathSegments,
  resolvePathAgainst,
  separatorAfter,
} from "./pathParts";

describe("separatorAfter", () => {
  // Mutation: draw a separator after every crumb -> `//tmp/x`, `C://Users`.
  it.each([
    ["/tmp/x", "/tmp/x"],
    ["/", "/"],
    ["~/a/b", "~/a/b"],
    ["C:/Users/me", "C:/Users/me"],
    ["//srv/sh/d", "//srv/sh/d"],
  ])("the crumbs of %s read back as %s", (path, text) => {
    const crumbs = pathSegments(path);
    const drawn = crumbs.map((c, i) => c.label + (i < crumbs.length - 1 && separatorAfter(c) ? "/" : "")).join("");
    expect(drawn).toBe(text);
  });

  it("a label that ends in a slash is followed by no separator; any other is", () => {
    expect(separatorAfter({ label: "/", path: "/" })).toBe(false);
    expect(separatorAfter({ label: "C:/", path: "C:/" })).toBe(false);
    expect(separatorAfter({ label: "tmp", path: "/tmp" })).toBe(true);
    expect(separatorAfter({ label: "~", path: "~" })).toBe(true);
  });
});

describe("parentPath", () => {
  it.each([
    ["/home/me", "/home"],
    ["/home", "/"],
    ["/home/me/", "/home"],
    ["~/x", "~"],
    ["~/a/b", "~/a"],
    ["C:/Users/me", "C:/Users"],
    ["C:/Users", "C:/"],
    ["C:/Users/", "C:/"],
    ["c:/x", "c:/"],
    ["C:\\Users\\me", "C:/Users"],
    ["//server/share/dir", "//server/share/"],
    ["//server/share/a/b", "//server/share/a"],
    ["/tmp/a\\b", "/tmp"],
  ])("the parent of %s is %s", (path, parent) => {
    expect(parentPath(path)).toBe(parent);
  });

  it.each(["/", "~", "C:/", "C:", "c:/", "//server/share/", "//server/share"])(
    "%s has no parent",
    (path) => {
      expect(parentPath(path)).toBeNull();
    },
  );
});

describe("isPathRoot", () => {
  it.each(["/", "C:/", "c:\\", "C:", "//server/share/", "~"])("%s is a root", (p) => {
    expect(isPathRoot(p)).toBe(true);
  });
  it.each(["/home", "C:/Users", "~/x", "//server/share/d"])("%s is not a root", (p) => {
    expect(isPathRoot(p)).toBe(false);
  });
});

describe("pathSegments", () => {
  it("lists a POSIX path from its root", () => {
    expect(pathSegments("/a/b")).toEqual([
      { label: "/", path: "/" },
      { label: "a", path: "/a" },
      { label: "b", path: "/a/b" },
    ]);
  });
  it("starts a drive path at the drive root", () => {
    expect(pathSegments("C:/Users/me")).toEqual([
      { label: "C:/", path: "C:/" },
      { label: "Users", path: "C:/Users" },
      { label: "me", path: "C:/Users/me" },
    ]);
  });
  it("starts a share path at the share", () => {
    expect(pathSegments("//srv/sh/d")).toEqual([
      { label: "//srv/sh", path: "//srv/sh/" },
      { label: "d", path: "//srv/sh/d" },
    ]);
  });
  it("starts a home path at ~", () => {
    expect(pathSegments("~/x")).toEqual([
      { label: "~", path: "~" },
      { label: "x", path: "~/x" },
    ]);
  });
  it("keeps a backslash in a POSIX name inside its segment", () => {
    expect(pathSegments("/tmp/a\\b").map((s) => s.label)).toEqual(["/", "tmp", "a\\b"]);
  });
});

describe("absolute paths and resolution", () => {
  it("knows the three absolute forms", () => {
    expect(isAbsolutePath("/x")).toBe(true);
    expect(isAbsolutePath("C:\\x")).toBe(true);
    expect(isAbsolutePath("\\\\srv\\x")).toBe(true);
    expect(isAbsolutePath("x/y")).toBe(false);
  });
  it("collapses dots and keeps the drive root", () => {
    expect(normalizeAbsolute("C:\\a\\..\\..\\b")).toBe("C:/b");
    expect(normalizeAbsolute("C:/a/..")).toBe("C:/");
    expect(normalizeAbsolute("/a/./b/..")).toBe("/a");
  });
  it("resolves against a drive base without a leading slash", () => {
    expect(resolvePathAgainst("../x.txt", "C:/Users/me")).toBe("C:/Users/x.txt");
    expect(resolvePathAgainst("x", "/srv")).toBe("/srv/x");
  });
  it("keeps a backslash in a POSIX name when it resolves", () => {
    expect(resolvePathAgainst("x", "/tmp/a\\b")).toBe("/tmp/a\\b/x");
    expect(normalizeAbsolute("/tmp/a\\b/../c")).toBe("/tmp/c");
    expect(normalizeAbsolute("/tmp/a\\b")).toBe("/tmp/a\\b");
  });
  it("keeps the share of a share base", () => {
    expect(resolvePathAgainst("x", "//srv/sh/d")).toBe("//srv/sh/d/x");
    expect(resolvePathAgainst("../../x", "//srv/sh/d")).toBe("//srv/sh/x");
    expect(normalizeAbsolute("//srv/sh/d/../x")).toBe("//srv/sh/x");
    expect(normalizeAbsolute("\\\\srv\\sh\\d")).toBe("//srv/sh/d");
  });
  it("keeps ~ as the root of a home base", () => {
    expect(resolvePathAgainst("x", "~/docs")).toBe("~/docs/x");
    expect(resolvePathAgainst("x", "~")).toBe("~/x");
    expect(resolvePathAgainst("../x", "~/docs")).toBe("~/x");
  });
  it("reads a backslash as a separator only under a drive or share base", () => {
    expect(resolvePathAgainst("docs\\x.md", "C:/Users/me")).toBe("C:/Users/me/docs/x.md");
    expect(resolvePathAgainst("docs\\x.md", "/home/me")).toBe("/home/me/docs\\x.md");
  });
  it("has one reading of a drive across the module", () => {
    for (const p of ["C:", "C:/", "C:\\", "C:/x"]) {
      expect(isAbsolutePath(p)).toBe(true);
      expect(isWindowsPath(p)).toBe(true);
    }
    expect(isAbsolutePath("C:x")).toBe(false);
    expect(normalizeAbsolute("C:")).toBe("C:/");
  });
});
