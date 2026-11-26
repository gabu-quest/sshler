/**
 * What the page starts when it is mounted: the progress and ping sockets, the
 * progress bars and the recovery poll all need a session, so a page that is not
 * signed in (the sign-in page) starts none of them.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { flushPromises, mount } from "@vue/test-utils";
import type { GlobalThemeOverrides } from "naive-ui";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { bootstrap, progress, ping, fetchRecoveryMock, currentRoute } = vi.hoisted(() => ({
  currentRoute: { path: "/login" },
  bootstrap: {
    payload: {} as object | null,
    token: "t" as string | null,
    authRequired: true,
    authenticated: true,
    get signedIn() { return !this.authRequired || this.authenticated; },
    bootstrap: vi.fn(async () => {}),
  },
  progress: { connect: vi.fn(), refresh: vi.fn(async () => {}), disconnect: vi.fn() },
  ping: { connect: vi.fn(), disconnect: vi.fn() },
  fetchRecoveryMock: vi.fn(async () => []),
}));

vi.mock("@/stores/bootstrap", () => ({ useBootstrapStore: () => bootstrap }));
vi.mock("@/stores/progress", () => ({ useProgressStore: () => progress }));
vi.mock("@/stores/ping", () => ({ usePingStore: () => ping }));
vi.mock("@/stores/app", () => ({ useAppStore: () => ({ isDark: false }) }));
vi.mock("@/api/http", () => ({ fetchRecovery: fetchRecoveryMock }));
vi.mock("vue-router", () => ({
  RouterView: { template: "<div />" },
  useRoute: () => currentRoute,
}));
vi.mock("@/components/AppHeader.vue", () => ({ default: { template: "<div />" } }));
vi.mock("@/components/PingNotificationHandler.vue", () => ({ default: { template: "<div />" } }));
vi.mock("@/components/ProgressStrip.vue", () => ({ default: { template: "<div />" } }));
vi.mock("@/components/RecoveryModal.vue", () => ({ default: { template: "<div />" } }));
vi.mock("@/config/naive-theme", () => ({ lightThemeOverrides: {}, darkThemeOverrides: {} }));
vi.mock("naive-ui", () => {
  const wrap = { template: "<div><slot /></div>" };
  return {
    NConfigProvider: wrap,
    NDialogProvider: wrap,
    NGlobalStyle: { template: "<div />" },
    NLayout: wrap,
    // The main area's scroll container is the element Naive styles with `content-style`.
    NLayoutContent: {
      props: ["contentStyle"],
      template: '<div data-testid="main-scroll" :style="contentStyle"><slot /></div>',
    },
    NLayoutHeader: wrap,
    NLoadingBarProvider: wrap,
    NMessageProvider: wrap,
    NNotificationProvider: wrap,
    darkTheme: {},
    lightTheme: {},
  };
});

import App from "./App.vue";

async function mountApp() {
  const wrapper = mount(App);
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  vi.clearAllMocks();
  currentRoute.path = "/login";
  bootstrap.token = "t";
  bootstrap.authRequired = true;
  bootstrap.authenticated = true;
});

describe("App on mount", () => {
  it("signed out: connects no socket, refreshes no bars and starts no recovery poll", async () => {
    bootstrap.authenticated = false;
    bootstrap.token = null;
    const wrapper = await mountApp();
    expect(progress.connect).toHaveBeenCalledTimes(0);
    expect(progress.refresh).toHaveBeenCalledTimes(0);
    expect(ping.connect).toHaveBeenCalledTimes(0);
    expect(fetchRecoveryMock).toHaveBeenCalledTimes(0);
    wrapper.unmount();
  });

  it("signed in: each starts once, with the token", async () => {
    const wrapper = await mountApp();
    expect(progress.connect.mock.calls).toEqual([["t"]]);
    expect(progress.refresh.mock.calls).toEqual([["t"]]);
    expect(ping.connect.mock.calls).toEqual([["t"]]);
    expect(fetchRecoveryMock.mock.calls).toEqual([["t"]]);
    wrapper.unmount();
  });

  it("no password set: each starts once", async () => {
    bootstrap.authRequired = false;
    bootstrap.authenticated = false;
    const wrapper = await mountApp();
    expect(progress.connect).toHaveBeenCalledTimes(1);
    expect(progress.refresh).toHaveBeenCalledTimes(1);
    expect(ping.connect).toHaveBeenCalledTimes(1);
    expect(fetchRecoveryMock).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });
});

describe("the main area", () => {
  it.each([
    ["/terminal", "hidden"],
    ["/multi-terminal", "hidden"],
    ["/commander", "hidden"],
    ["/", "auto"],
    ["/files", "auto"],
    ["/settings", "auto"],
    ["/login", "auto"],
  ])("on %s its overflow is %s", async (path, overflow) => {
    currentRoute.path = path;
    const wrapper = await mountApp();
    const scroll = wrapper.get('[data-testid="main-scroll"]').element as HTMLElement;
    expect(scroll.style.overflowY).toBe(overflow);
    expect(scroll.style.minHeight).toBe("0px");
    wrapper.unmount();
  });

  it("no rule in the page frame's styles fixes a height or hides overflow", () => {
    const source = readFileSync(resolve(__dirname, "App.vue"), "utf-8");
    const style = source.slice(source.indexOf("<style"));
    expect(style.match(/calc\([^)]*-\s*\d+px\)/g)).toBeNull();
    expect(style.match(/overflow\s*:\s*hidden/g)).toBeNull();
  });
});

/** The custom properties of the first CSS block whose selector is exactly `selector`. */
function tokensOf(css: string, selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no block ${selector}`);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  const tokens: Record<string, string> = {};
  for (const match of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    tokens[match[1]!] = match[2]!.trim();
  }
  return tokens;
}

const mainCss = readFileSync(resolve(__dirname, "styles/main.css"), "utf-8");
const lightTokens = tokensOf(mainCss, '[data-theme="light"]');
const darkTokens = tokensOf(mainCss, '.dark, [data-theme="dark"]');

// Each Naive UI colour and the token in main.css it must equal.
const PAIRS: Array<[keyof NonNullable<GlobalThemeOverrides["common"]>, string]> = [
  ["primaryColor", "--accent"],
  ["primaryColorSuppl", "--accent"],
  ["primaryColorHover", "--accent-hover"],
  ["primaryColorPressed", "--accent-pressed"],
  ["bodyColor", "--bg"],
  ["cardColor", "--surface"],
  ["tableColor", "--surface"],
  ["tableColorStriped", "--table-stripe"],
  ["modalColor", "--surface-raised"],
  ["popoverColor", "--surface-raised"],
  ["inputColor", "--input-bg"],
  ["borderColor", "--stroke"],
  ["dividerColor", "--stroke"],
  ["textColor1", "--text"],
  ["textColor2", "--text-secondary"],
  ["textColor3", "--muted"],
  ["successColor", "--success"],
  ["successColorHover", "--success-hover"],
  ["warningColor", "--warning"],
  ["warningColorHover", "--warning-hover"],
  ["errorColor", "--error"],
  ["errorColorHover", "--error-hover"],
  ["borderRadius", "--radius-md"],
];

describe("the Naive UI theme and the CSS tokens", () => {
  it("one accent: the dark and light accents are the chosen blues", () => {
    expect(darkTokens["--accent"]).toBe("#5b9dff");
    expect(lightTokens["--accent"]).toBe("#2563eb");
    expect(lightTokens["--bg"]).toBe("#f4f5f7");
    expect(lightTokens["--surface"]).toBe("#ffffff");
    expect(darkTokens["--bg"]).toBe("#111317");
    expect(darkTokens["--radius-md"] ?? tokensOf(mainCss, ":root")["--radius-md"]).toBe("6px");
  });

  it.each(PAIRS)("dark %s equals %s", async (key, token) => {
    const { darkThemeOverrides } = await vi.importActual<typeof import("@/config/naive-theme")>(
      "@/config/naive-theme",
    );
    const value = darkTokens[token] ?? tokensOf(mainCss, ":root")[token];
    expect(value).toMatch(/^(#[0-9a-f]{6}|\d+px)$/);
    expect(darkThemeOverrides.common?.[key]).toBe(value);
  });

  it.each(PAIRS)("light %s equals %s", async (key, token) => {
    const { lightThemeOverrides } = await vi.importActual<typeof import("@/config/naive-theme")>(
      "@/config/naive-theme",
    );
    const value = lightTokens[token] ?? tokensOf(mainCss, ":root")[token];
    expect(value).toMatch(/^(#[0-9a-f]{6}|\d+px)$/);
    expect(lightThemeOverrides.common?.[key]).toBe(value);
  });

  it.each([
    ["light", "lightThemeOverrides", lightTokens],
    ["dark", "darkThemeOverrides", darkTokens],
  ] as const)("every colour in the %s Naive overrides, in every component, is a token value", async (_name, exportName, tokens) => {
    const module = await vi.importActual<typeof import("@/config/naive-theme")>("@/config/naive-theme");
    const allowed = new Set(Object.values(tokens).map((value) => value.toLowerCase()));
    const found: Array<[string, string]> = [];
    const walk = (node: unknown, path: string): void => {
      if (typeof node === "string") {
        for (const hex of node.match(/#[0-9a-fA-F]{6}\b/g) ?? []) found.push([path, hex.toLowerCase()]);
      } else if (node && typeof node === "object") {
        for (const [key, child] of Object.entries(node)) walk(child, `${path}.${key}`);
      }
    };
    walk(module[exportName], exportName);
    expect(found.length).toBeGreaterThan(20);
    expect(found.filter(([, hex]) => !allowed.has(hex))).toEqual([]);
  });

  it("the defaults before a theme is set are the light theme, and system dark is the dark theme", () => {
    const root = tokensOf(mainCss, ":root");
    const systemDark = tokensOf(mainCss, "@media (prefers-color-scheme: dark) {\n  :root");
    for (const [name, value] of Object.entries(lightTokens)) expect([name, root[name]]).toEqual([name, value]);
    expect(Object.keys(systemDark).sort()).toEqual(Object.keys(darkTokens).sort());
    for (const [name, value] of Object.entries(darkTokens)) expect([name, systemDark[name]]).toEqual([name, value]);
  });

  it("the light page has no dot grid", () => {
    expect(mainCss).not.toMatch(/radial-gradient/);
  });

  it("the shell files use no undefined variable names", () => {
    const files = ["App.vue", "styles/main.css", "styles/responsive.css", "config/naive-theme.ts"];
    const banned = /var\(--(hover|divider|text-color|body-color|font-family-mono)\b/g;
    const found = files.flatMap((file) =>
      (readFileSync(resolve(__dirname, file), "utf-8").match(banned) ?? []).map((m) => `${file}: ${m}`),
    );
    expect(found).toEqual([]);
  });
});
