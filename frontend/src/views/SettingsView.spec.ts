/**
 * The settings view's sign-out row: shown only when a password is set, it names the
 * signed-in user, and its button ends the session and loads the sign-in page.
 */
import { fireEvent, render, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia, type Pinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import { useAuthStore } from "@/stores/auth";
import { useBootstrapStore } from "@/stores/bootstrap";
import SettingsView from "./SettingsView.vue";

const { messageSpy, httpMock, loadPageMock } = vi.hoisted(() => ({
  messageSpy: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  httpMock: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  loadPageMock: vi.fn(),
}));

vi.mock("@/api/http", () => ({
  http: httpMock,
  buildHeaders: () => ({}),
  fetchBootstrap: vi.fn(),
  fetchSnapshotConfig: vi.fn(async () => ({ enabled: true, interval: 30 })),
  updateSnapshotConfig: vi.fn(),
}));
vi.mock("@/utils/pageLoad", () => ({ loadPage: loadPageMock }));
vi.mock("vue-router", () => ({
  useRouter: () => ({ resolve: (to: { name: string }) => ({ href: `/app/${to.name}` }) }),
}));

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  return {
    NAlert: stub('<div class="stub-alert"><slot /></div>'),
    NButton: stub('<button class="stub-button" @click="$emit(\'click\')"><slot /></button>', [], ["click"]),
    NCard: stub('<div class="stub-card"><slot /></div>'),
    NCode: stub("<code><slot /></code>"),
    NInputNumber: stub("<input />"),
    NSelect: stub("<select />"),
    NSpace: stub("<div><slot /></div>"),
    NSwitch: stub("<input type=\"checkbox\" />"),
    useMessage: () => messageSpy,
  };
});

let pinia: Pinia;

function signedIn(authRequired: boolean): void {
  const bootstrap = useBootstrapStore();
  bootstrap.payload = {
    version: "1", token_header: "X-SSHLER-TOKEN", token: "tok", auth_required: authRequired,
    authenticated: true, allow_origins: [], spa_base: "/app/", spa_enabled: true,
  };
  bootstrap.setToken("tok");
  if (authRequired) useAuthStore().setUser("admin");
}

function renderView() {
  return render(SettingsView, { global: { plugins: [pinia] } });
}

function signOutButton(container: Element): HTMLElement | undefined {
  return Array.from(container.querySelectorAll<HTMLElement>(".stub-button")).find(
    (b) => b.textContent?.trim() === "Sign out",
  );
}

describe("SettingsView: the sign-out row", () => {
  beforeEach(() => {
    // The app store reads window.matchMedia for the colour mode; jsdom lacks it.
    if (!window.matchMedia) {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: (query: string) => ({
          matches: false,
          media: query,
          onchange: null,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
          dispatchEvent: () => false,
        }),
      });
    }
    pinia = createPinia();
    setActivePinia(pinia);
    vi.clearAllMocks();
    httpMock.get.mockResolvedValue({ data: { idle_timeout: null, max_lifetime: null, max_connections_per_box: 3 } });
  });

  it("names the signed-in user when a password is set", async () => {
    signedIn(true);
    const { getByTestId } = renderView();
    const row = await waitFor(() => getByTestId("sign-out-row"));
    expect(row.querySelector("span")!.textContent!.trim()).toBe("Signed in as admin");
    expect(signOutButton(row)!.textContent!.trim()).toBe("Sign out");
  });

  it("is not shown when no password is set", async () => {
    signedIn(false);
    const { container, queryByTestId } = renderView();
    await waitFor(() => expect(httpMock.get).toHaveBeenCalledTimes(1));
    expect(queryByTestId("sign-out-row")).toBeNull();
    expect(signOutButton(container)).toBeUndefined();
  });

  it("signs out, forgets the page's token and loads the sign-in page", async () => {
    signedIn(true);
    httpMock.post.mockResolvedValueOnce({ data: { success: true, message: "Logged out successfully" } });
    const { getByTestId } = renderView();
    const row = await waitFor(() => getByTestId("sign-out-row"));

    await fireEvent.click(signOutButton(row)!);

    await waitFor(() => expect(loadPageMock).toHaveBeenCalledTimes(1));
    expect(httpMock.post).toHaveBeenCalledWith("/api/v1/auth/logout");
    expect(loadPageMock).toHaveBeenCalledWith("/app/login");
    expect(useBootstrapStore().token).toBeNull();
    expect(useAuthStore().isAuthenticated).toBe(false);
    expect(messageSpy.error).toHaveBeenCalledTimes(0);
  });

  it("a failed sign-out shows why and stays signed in", async () => {
    signedIn(true);
    httpMock.post.mockRejectedValueOnce(new ApiError(502, "unreachable", "proxy down"));
    const { getByTestId } = renderView();
    const row = await waitFor(() => getByTestId("sign-out-row"));

    await fireEvent.click(signOutButton(row)!);

    await waitFor(() => expect(messageSpy.error).toHaveBeenCalledTimes(1));
    expect(messageSpy.error).toHaveBeenCalledWith("Cannot connect to the box — proxy down");
    expect(loadPageMock).toHaveBeenCalledTimes(0);
    expect(useBootstrapStore().token).toBe("tok");
    expect(useAuthStore().username).toBe("admin");
  });
});
