/**
 * The sign-in page's failed sign-in: it acts on the failure's reason.
 */
import { render, fireEvent, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import * as httpModule from "@/api/http";
import LoginView from "./LoginView.vue";

// One router for the page and for the request function's sign-in redirect.
const router = vi.hoisted(() => ({
  push: vi.fn(),
  resolve: vi.fn((to: string) => ({ href: `/app${to}` })),
  currentRoute: { value: { name: "login", fullPath: "/login", query: {} as Record<string, string> } },
}));
vi.mock("vue-router", () => ({ useRouter: () => router }));
vi.mock("@/router", () => ({ default: router }));
const loadPageMock = vi.hoisted(() => vi.fn());
vi.mock("@/utils/pageLoad", () => ({ loadPage: loadPageMock }));

const post = vi.spyOn(httpModule.http, "post");

async function signIn(failure: unknown): Promise<string> {
  post.mockRejectedValueOnce(failure);
  const { container, getByPlaceholderText, getByRole } = render(LoginView, {
    global: { plugins: [createPinia()] },
  });
  await fireEvent.update(getByPlaceholderText("Enter username"), "u");
  await fireEvent.update(getByPlaceholderText("Enter password"), "p");
  await fireEvent.click(getByRole("button", { name: "Sign In" }));
  await waitFor(() => expect(container.querySelector(".n-alert")).not.toBeNull());
  return container.querySelector(".n-alert")!.textContent!.trim();
}

describe("LoginView: a failed sign-in", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    post.mockReset();
    router.push.mockClear();
    router.currentRoute.value.query = {};
  });

  it.each([
    ["auth", new ApiError(401, "auth", "Invalid username or password"), "Invalid username or password"],
    ["rate_limited", new ApiError(429, "rate_limited", "Too many failed authentication attempts. Try again later."),
      "Too many failed attempts. Please try again later"],
    ["another reason", new ApiError(502, "unreachable", "proxy down"), "Cannot connect to the box — proxy down"],
    ["a lockout of 300 s", new ApiError(429, "rate_limited", "Too many failed authentication attempts. Try again later.", 300),
      "Too many failed attempts. Try again in 5 min"],
    ["a lockout of 61 s", new ApiError(429, "rate_limited", "Too many failed authentication attempts. Try again later.", 61),
      "Too many failed attempts. Try again in 2 min"],
    ["a lockout of 60 s", new ApiError(429, "rate_limited", "Too many failed authentication attempts. Try again later.", 60),
      "Too many failed attempts. Try again in 1 min"],
    ["a lockout of 59 s", new ApiError(429, "rate_limited", "Too many failed authentication attempts. Try again later.", 59),
      "Too many failed attempts. Try again in 59 s"],
  ])("%s shows its line", async (_label, failure, shown) => {
    expect(await signIn(failure)).toBe(shown);
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("a wrong password through the real request shows its line and goes nowhere", async () => {
    post.mockRestore();
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ detail: "Invalid username or password", reason: "auth" }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    try {
      const { container, getByPlaceholderText, getByRole } = render(LoginView, {
        global: { plugins: [createPinia()] },
      });
      await fireEvent.update(getByPlaceholderText("Enter username"), "u");
      await fireEvent.update(getByPlaceholderText("Enter password"), "p");
      await fireEvent.click(getByRole("button", { name: "Sign In" }));
      await waitFor(() => expect(container.querySelector(".n-alert")).not.toBeNull());
      expect(container.querySelector(".n-alert")!.textContent!.trim()).toBe("Invalid username or password");
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(router.push).toHaveBeenCalledTimes(0);
      expect(loadPageMock).toHaveBeenCalledTimes(0);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("LoginView: a good sign-in", () => {
  // The spy of its own: a test above restores the shared one.
  let signInPost: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    setActivePinia(createPinia());
    signInPost = vi.spyOn(httpModule.http, "post");
    loadPageMock.mockReset();
    router.push.mockClear();
  });

  async function signInWell(): Promise<void> {
    signInPost.mockResolvedValueOnce({ data: { success: true, message: "Login successful" } });
    const { getByPlaceholderText, getByRole } = render(LoginView, {
      global: { plugins: [createPinia()] },
    });
    await fireEvent.update(getByPlaceholderText("Enter username"), "admin");
    await fireEvent.update(getByPlaceholderText("Enter password"), "pw");
    await fireEvent.click(getByRole("button", { name: "Sign In" }));
    await waitFor(() => expect(loadPageMock).toHaveBeenCalledTimes(1));
  }

  it("loads the page afresh at redirect", async () => {
    router.currentRoute.value.query = { redirect: "/terminal?box=web" };
    await signInWell();
    expect(signInPost).toHaveBeenCalledWith("/api/v1/auth/login", { username: "admin", password: "pw" });
    expect(loadPageMock).toHaveBeenCalledWith("/app/terminal?box=web");
    expect(router.push).toHaveBeenCalledTimes(0);
  });

  it.each([
    ["another site", "https://example.com/"],
    ["a protocol-relative address", "//example.com/"],
    ["no redirect", undefined],
  ])("goes to the overview for %s", async (_label, redirect) => {
    router.currentRoute.value.query = redirect === undefined ? {} : { redirect };
    await signInWell();
    expect(loadPageMock).toHaveBeenCalledWith("/app/");
  });
});
