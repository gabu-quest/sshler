import { describe, expect, it, vi } from "vitest";

// The suite's setup replaces "@/i18n" with a stand-in; this spec is about the real one.
const { translate } = await vi.importActual<typeof import("@/i18n")>("@/i18n");

describe("translate: a value is put in as it is", () => {
  // Mutation: the value is given to String.replace as a replacement pattern, where "$$"
  // means one "$" and "$&" means the placeholder itself.
  it.each([
    ["cost$$ (2).txt", "Kept both: saved as cost$$ (2).txt"],
    ["a$&b.txt", "Kept both: saved as a$&b.txt"],
    ["$1 $` $'.txt", "Kept both: saved as $1 $` $'.txt"],
  ])("a file name holding %s", (name, text) => {
    expect(translate("files.kept_both", { name })).toBe(text);
  });

  it("every occurrence of a placeholder is filled", () => {
    expect(translate("files.replace_same_for_rest", { n: 2 })).toBe("Do the same for the other 2");
  });
});
