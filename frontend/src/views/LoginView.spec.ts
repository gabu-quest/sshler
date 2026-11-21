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
  currentRoute: { value: { name: "login", fullPath: "/login", query: {} } },
}));
vi.mock("vue-router", () => ({ useRouter: () => router }));
vi.mock("@/router", () => ({ default: router }));

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
  });

  it.each([
    ["auth", new ApiError(401, "auth", "Invalid username or password"), "Invalid username or password"],
    ["rate_limited", new ApiError(429, "rate_limited", "Too many failed authentication attempts. Try again later."),
      "Too many failed attempts. Please try again later"],
    ["another reason", new ApiError(502, "unreachable", "proxy down"), "Cannot connect to the box — proxy down"],
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
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
