/**
 * What the page starts when it is mounted: the progress and ping sockets, the
 * progress bars and the recovery poll all need a session, so a page that is not
 * signed in (the sign-in page) starts none of them.
 */
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { bootstrap, progress, ping, fetchRecoveryMock } = vi.hoisted(() => ({
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
  useRoute: () => ({ path: "/login" }),
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
    NLayoutContent: wrap,
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
