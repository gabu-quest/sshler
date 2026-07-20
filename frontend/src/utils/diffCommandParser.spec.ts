import { describe, expect, it } from "vitest";

import { parseCommand, parseSide } from "./diffCommandParser";

describe("parseCommand", () => {
  describe(":add", () => {
    it("with no args returns null sides (prefill from previous)", () => {
      const r = parseCommand(":add");
      expect(r).toEqual({ type: "add", left: null, right: null });
    });

    it("with one side fills only left", () => {
      const r = parseCommand(":add local:/r@main:src/a.ts");
      expect(r).toEqual({
        type: "add",
        left: { box: "local", directory: "/r", ref: "main", path: "src/a.ts" },
        right: null,
      });
    });

    it("with two sides fills both", () => {
      const r = parseCommand(":add local:/r@main:a.ts local:/r@feat:a.ts");
      expect(r).toEqual({
        type: "add",
        left: { box: "local", directory: "/r", ref: "main", path: "a.ts" },
        right: { box: "local", directory: "/r", ref: "feat", path: "a.ts" },
      });
    });

    it("rejects three or more sides", () => {
      const r = parseCommand(":add a b c");
      expect(r).toEqual({ type: "error", message: ":add takes at most two side specs." });
    });

    it("accepts double-quoted paths with spaces", () => {
      const r = parseCommand(':add "local:/r@main:my file.ts"');
      expect(r).toEqual({
        type: "add",
        left: { box: "local", directory: "/r", ref: "main", path: "my file.ts" },
        right: null,
      });
    });
  });

  describe(":rm", () => {
    it("parses a cell number", () => {
      expect(parseCommand(":rm 3")).toEqual({ type: "rm", index: 3 });
    });

    it("rejects no argument", () => {
      const r = parseCommand(":rm");
      expect(r).toEqual({ type: "error", message: ":rm requires a cell number." });
    });

    it("rejects non-numeric argument", () => {
      const r = parseCommand(":rm foo");
      expect(r).toEqual({ type: "error", message: "Invalid cell number: foo" });
    });

    it("rejects zero", () => {
      const r = parseCommand(":rm 0");
      expect(r).toEqual({ type: "error", message: "Invalid cell number: 0" });
    });

    it("accepts the `:remove` alias", () => {
      expect(parseCommand(":remove 2")).toEqual({ type: "rm", index: 2 });
    });
  });

  describe(":swap", () => {
    it("one arg swaps sides of one cell", () => {
      expect(parseCommand(":swap 2")).toEqual({ type: "swap", index: 2, other: null });
    });

    it("two args swap two cells", () => {
      expect(parseCommand(":swap 1 3")).toEqual({ type: "swap", index: 1, other: 3 });
    });

    it("rejects identical indices", () => {
      const r = parseCommand(":swap 2 2");
      expect(r).toEqual({ type: "error", message: ":swap needs two different cell numbers." });
    });

    it("rejects three or more args", () => {
      const r = parseCommand(":swap 1 2 3");
      expect(r).toEqual({ type: "error", message: ":swap takes one or two cell numbers." });
    });
  });

  describe(":repo", () => {
    it("parses box + directory", () => {
      expect(parseCommand(":repo local /home/me/repo")).toEqual({
        type: "repo",
        box: "local",
        directory: "/home/me/repo",
      });
    });

    it("rejects missing args", () => {
      expect(parseCommand(":repo")).toEqual({
        type: "error",
        message: ":repo requires a box and a directory.",
      });
      expect(parseCommand(":repo local")).toEqual({
        type: "error",
        message: ":repo takes exactly two arguments: <box> <dir>.",
      });
    });
  });

  describe(":clear / :help", () => {
    it(":clear with no args", () => {
      expect(parseCommand(":clear")).toEqual({ type: "clear" });
    });

    it(":clear with args is an error", () => {
      const r = parseCommand(":clear extra");
      expect(r).toEqual({ type: "error", message: ":clear takes no arguments." });
    });

    it("? alone is help", () => {
      expect(parseCommand("?")).toEqual({ type: "help" });
    });

    it(":help is help", () => {
      expect(parseCommand(":help")).toEqual({ type: "help" });
    });
  });

  describe("dispatch errors", () => {
    it("empty input is an error", () => {
      expect(parseCommand("")).toEqual({ type: "error", message: "Empty command." });
      expect(parseCommand("   ")).toEqual({ type: "error", message: "Empty command." });
    });

    it("unknown command is an error", () => {
      expect(parseCommand(":foobar")).toEqual({
        type: "error",
        message: "Unknown command: :foobar",
      });
    });

    it("accepts commands without leading colon", () => {
      expect(parseCommand("clear")).toEqual({ type: "clear" });
      expect(parseCommand("rm 1")).toEqual({ type: "rm", index: 1 });
    });

    it("is whitespace-tolerant", () => {
      expect(parseCommand("  :rm    2  ")).toEqual({ type: "rm", index: 2 });
    });
  });
});

describe("parseSide", () => {
  it("full form", () => {
    expect(parseSide("local:/r@main:src/a.ts")).toEqual({
      box: "local",
      directory: "/r",
      ref: "main",
      path: "src/a.ts",
    });
  });

  it("box only", () => {
    expect(parseSide("local")).toEqual({ box: "local", directory: "", ref: "", path: "" });
  });

  it("box + dir", () => {
    expect(parseSide("local:/r")).toEqual({ box: "local", directory: "/r", ref: "", path: "" });
  });

  it("ref + path only (leading colon-at)", () => {
    expect(parseSide(":@feat:src/a.ts")).toEqual({
      box: "",
      directory: "",
      ref: "feat",
      path: "src/a.ts",
    });
  });

  it("preserves colons inside path", () => {
    expect(parseSide("local:/r@main:weird:filename.ts")).toEqual({
      box: "local",
      directory: "/r",
      ref: "main",
      path: "weird:filename.ts",
    });
  });

  it("ref without path", () => {
    expect(parseSide("local:/r@main")).toEqual({
      box: "local",
      directory: "/r",
      ref: "main",
      path: "",
    });
  });

  it("empty input is an error", () => {
    expect(parseSide("")).toEqual({ type: "error", message: "Empty side spec." });
  });
});
