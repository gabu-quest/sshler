import { describe, it, expect } from "vitest";
import { classifyTerminalCloseCode } from "./terminalCloseCodes";

describe("classifyTerminalCloseCode", () => {
  it("4504 (session cap) shows the too-many-terminals message and does NOT reconnect", () => {
    // Regression: the ConPTY session cap closes /ws/term with 4504. Without an
    // explicit case this fell through to the default (reconnect:true), so the
    // client would retry forever and re-hit the cap on every attempt.
    const policy = classifyTerminalCloseCode(4504);
    expect(policy.reconnect).toBe(false);
    expect(policy.messageKey).toBe("terminal.too_many_terminals");
  });

  it("4403 (bad CSRF token) toasts auth_failed and stops reconnecting", () => {
    const policy = classifyTerminalCloseCode(4403);
    expect(policy.reconnect).toBe(false);
    expect(policy.messageKey).toBe("terminal.auth_failed");
    expect(policy.duration).toBe(5000);
  });

  it("4401 (unauthorized) toasts auth_denied and stops reconnecting", () => {
    const policy = classifyTerminalCloseCode(4401);
    expect(policy.reconnect).toBe(false);
    expect(policy.messageKey).toBe("terminal.auth_denied");
  });

  it("4502 (no WSL distro) toasts wsl_not_installed and stops reconnecting", () => {
    const policy = classifyTerminalCloseCode(4502);
    expect(policy.reconnect).toBe(false);
    expect(policy.messageKey).toBe("terminal.wsl_not_installed");
  });

  it("4503 (pywinpty missing) toasts pywinpty_missing and stops reconnecting", () => {
    const policy = classifyTerminalCloseCode(4503);
    expect(policy.reconnect).toBe(false);
    expect(policy.messageKey).toBe("terminal.pywinpty_missing");
  });

  it("1000 (normal closure) is silent and does NOT reconnect", () => {
    const policy = classifyTerminalCloseCode(1000);
    expect(policy.reconnect).toBe(false);
    expect(policy.messageKey).toBe(null);
  });

  it("an unexpected code reconnects with no toast", () => {
    const policy = classifyTerminalCloseCode(1006);
    expect(policy.reconnect).toBe(true);
    expect(policy.messageKey).toBe(null);
  });
});
