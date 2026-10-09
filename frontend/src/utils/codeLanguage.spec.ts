import { describe, it, expect } from "vitest";
import { getLanguageForFilename } from "./codeLanguage";

describe("getLanguageForFilename", () => {
  it("maps TypeScript files to their own ids (not javascript)", () => {
    expect(getLanguageForFilename("foo.ts")).toBe("typescript");
    expect(getLanguageForFilename("foo.tsx")).toBe("tsx");
  });

  it("maps JavaScript files", () => {
    expect(getLanguageForFilename("foo.js")).toBe("javascript");
    expect(getLanguageForFilename("foo.jsx")).toBe("jsx");
  });

  it("maps systems languages", () => {
    expect(getLanguageForFilename("main.go")).toBe("go");
    expect(getLanguageForFilename("lib.rs")).toBe("rust");
    expect(getLanguageForFilename("app.c")).toBe("c");
    expect(getLanguageForFilename("app.h")).toBe("c");
    expect(getLanguageForFilename("app.cpp")).toBe("cpp");
    expect(getLanguageForFilename("App.cs")).toBe("csharp");
    expect(getLanguageForFilename("Main.java")).toBe("java");
    expect(getLanguageForFilename("Main.kt")).toBe("kotlin");
    expect(getLanguageForFilename("App.swift")).toBe("swift");
  });

  it("maps shell scripts", () => {
    expect(getLanguageForFilename("run.sh")).toBe("shell");
    expect(getLanguageForFilename("run.bash")).toBe("shell");
    expect(getLanguageForFilename("run.zsh")).toBe("shell");
  });

  it("maps data/config formats", () => {
    expect(getLanguageForFilename("config.yaml")).toBe("yaml");
    expect(getLanguageForFilename("config.yml")).toBe("yaml");
    expect(getLanguageForFilename("Cargo.toml")).toBe("toml");
    expect(getLanguageForFilename("settings.ini")).toBe("ini");
  });

  it("maps SQL files", () => {
    expect(getLanguageForFilename("query.sql")).toBe("sql");
  });

  it("maps scripting languages", () => {
    expect(getLanguageForFilename("script.rb")).toBe("ruby");
    expect(getLanguageForFilename("index.php")).toBe("php");
    expect(getLanguageForFilename("script.lua")).toBe("lua");
    expect(getLanguageForFilename("script.pl")).toBe("perl");
    expect(getLanguageForFilename("stats.r")).toBe("r");
  });

  it("maps existing/pre-existing web + doc formats unchanged", () => {
    expect(getLanguageForFilename("index.html")).toBe("html");
    expect(getLanguageForFilename("index.htm")).toBe("html");
    expect(getLanguageForFilename("style.css")).toBe("css");
    expect(getLanguageForFilename("style.scss")).toBe("css");
    expect(getLanguageForFilename("data.json")).toBe("json");
    expect(getLanguageForFilename("README.md")).toBe("markdown");
    expect(getLanguageForFilename("doc.xml")).toBe("xml");
    expect(getLanguageForFilename("icon.svg")).toBe("xml");
    expect(getLanguageForFilename("App.vue")).toBe("vue");
  });

  it("special-cases well-known extensionless basenames", () => {
    expect(getLanguageForFilename("Dockerfile")).toBe("dockerfile");
    expect(getLanguageForFilename("dockerfile")).toBe("dockerfile");
    expect(getLanguageForFilename("Makefile")).toBe("makefile");
    expect(getLanguageForFilename("makefile")).toBe("makefile");
    expect(getLanguageForFilename("justfile")).toBe("makefile");
  });

  it("resolves basename special-cases from a full path", () => {
    expect(getLanguageForFilename("/srv/app/Dockerfile")).toBe("dockerfile");
    expect(getLanguageForFilename("/srv/app/src/main.go")).toBe("go");
  });

  it("falls back to text for unknown extensions and extensionless/dotfiles", () => {
    expect(getLanguageForFilename("unknown.xyz")).toBe("text");
    expect(getLanguageForFilename("noextension")).toBe("text");
    expect(getLanguageForFilename(".gitignore")).toBe("text");
  });
});
