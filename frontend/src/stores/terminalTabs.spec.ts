import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import {
  useTerminalTabsStore,
  generateSessionName,
  lastPathSegment,
  scopeKey,
} from "./terminalTabs";

// vitest.setup.ts installs a real in-memory localStorage stub and clears it
// in beforeEach, so localStorage.clear() here is belt-and-suspenders.

// Store methods take an opaque scope string; tests pass plain strings as scopes.
const BOX = "mybox";
const SEED = { directory: "/srv/app", shell: "bash" };

describe("terminalTabs store", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  // ---------------------------------------------------------------------------
  // 1. loadScope with no stored data creates exactly ONE tab
  // ---------------------------------------------------------------------------

  it("loadScope with no stored data creates one seed tab", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);

    const tabList = store.tabs(BOX);
    expect(tabList).toHaveLength(1);

    const tab = tabList[0]!;
    expect(tab.directory).toBe("/srv/app");
    expect(tab.shell).toBe("bash");
    expect(tab.sessionName).toBe("app");
    expect(store.activeTabId(BOX)).toBe(tab.id);
  });

  // ---------------------------------------------------------------------------
  // 2. addTab appends and activates by default
  // ---------------------------------------------------------------------------

  it("addTab appends a tab and activates it by default", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);

    const tab2 = store.addTab(BOX, { directory: "/home/user", shell: "zsh" });

    const tabList = store.tabs(BOX);
    expect(tabList).toHaveLength(2);
    expect(tabList[1]!.id).toBe(tab2.id);
    expect(tabList[1]!.directory).toBe("/home/user");
    expect(tabList[1]!.shell).toBe("zsh");
    expect(store.activeTab(BOX)!.id).toBe(tab2.id);
  });

  // ---------------------------------------------------------------------------
  // 3. Duplicate session-name suffixing: home → home-2 → home-3
  // ---------------------------------------------------------------------------

  it("suffixes duplicate session names with -2, -3, …", () => {
    const store = useTerminalTabsStore();
    // Seed with directory "~" → sessionName "home"
    store.loadScope(BOX, { directory: "~", shell: "bash" });
    expect(store.tabs(BOX)[0]!.sessionName).toBe("home");

    const tab2 = store.addTab(BOX, { directory: "~", shell: "bash" });
    expect(tab2.sessionName).toBe("home-2");

    const tab3 = store.addTab(BOX, { directory: "~", shell: "bash" });
    expect(tab3.sessionName).toBe("home-3");
  });

  // ---------------------------------------------------------------------------
  // 4. addTab with activate:false does NOT change activeTabId
  // ---------------------------------------------------------------------------

  it("addTab with activate:false does not change activeTabId", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    const firstId = store.activeTabId(BOX)!;
    expect(firstId).not.toBeNull();

    store.addTab(BOX, { directory: "/tmp", shell: "sh", activate: false });

    expect(store.tabs(BOX)).toHaveLength(2);
    expect(store.activeTabId(BOX)).toBe(firstId);
  });

  // ---------------------------------------------------------------------------
  // 5. closeTab removes the tab and activates a neighbour when active is closed
  // ---------------------------------------------------------------------------

  it("closeTab removes the tab and activates a neighbour when active is closed", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    const tab2 = store.addTab(BOX, { directory: "/var/log", shell: "bash" });
    const tab3 = store.addTab(BOX, { directory: "/etc", shell: "bash" });
    // tab3 is now active (last added)

    store.closeTab(BOX, tab3.id);

    const remaining = store.tabs(BOX);
    expect(remaining).toHaveLength(2);
    // After closing last tab, the new last tab (tab2) should be active
    expect(store.activeTabId(BOX)).toBe(tab2.id);
    expect(remaining.some((t) => t.id === tab3.id)).toBe(false);
  });

  // ---------------------------------------------------------------------------
  // 6. Closing the last tab recreates a fresh default tab
  // ---------------------------------------------------------------------------

  it("closing the last tab recreates a fresh default tab with a new id", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    const onlyTab = store.tabs(BOX)[0]!;
    const oldId = onlyTab.id;

    store.closeTab(BOX, oldId);

    const tabList = store.tabs(BOX);
    expect(tabList).toHaveLength(1);
    const newTab = tabList[0]!;
    expect(newTab.id).not.toBe(oldId);
    // Recreated from the closed tab's directory/shell
    expect(newTab.directory).toBe("/srv/app");
    expect(newTab.shell).toBe("bash");
    expect(store.activeTabId(BOX)).toBe(newTab.id);
  });

  // ---------------------------------------------------------------------------
  // 7. renameTab changes only the label; sessionName/directory unchanged
  // ---------------------------------------------------------------------------

  it("renameTab changes only the label", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    const tab = store.tabs(BOX)[0]!;
    const originalSessionName = tab.sessionName;
    const originalDirectory = tab.directory;

    store.renameTab(BOX, tab.id, "My Custom Label");

    const updated = store.tabs(BOX)[0]!;
    expect(updated.label).toBe("My Custom Label");
    expect(updated.sessionName).toBe(originalSessionName);
    expect(updated.directory).toBe(originalDirectory);
  });

  // ---------------------------------------------------------------------------
  // 8. openOrActivateSession: existing → activate without new tab;
  //    new sessionName → exact sessionName appended
  // ---------------------------------------------------------------------------

  it("openOrActivateSession re-activates an existing session without adding a tab", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    // Add a second tab so the first is not active
    store.addTab(BOX, { directory: "/tmp", shell: "bash" });
    expect(store.tabs(BOX)).toHaveLength(2);

    const firstTab = store.tabs(BOX)[0]!;
    store.openOrActivateSession(BOX, {
      sessionName: firstTab.sessionName,
      directory: firstTab.directory,
      shell: firstTab.shell,
    });

    expect(store.tabs(BOX)).toHaveLength(2);
    expect(store.activeTabId(BOX)).toBe(firstTab.id);
  });

  it("openOrActivateSession with a new sessionName appends a tab with that exact name", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);

    store.openOrActivateSession(BOX, {
      sessionName: "my-special-session",
      directory: "/opt/app",
      shell: "fish",
    });

    const tabList = store.tabs(BOX);
    expect(tabList).toHaveLength(2);
    const newTab = tabList[1]!;
    expect(newTab.sessionName).toBe("my-special-session");
    expect(newTab.directory).toBe("/opt/app");
    expect(newTab.shell).toBe("fish");
    expect(store.activeTabId(BOX)).toBe(newTab.id);
  });

  // ---------------------------------------------------------------------------
  // 9. Per-scope isolation
  // ---------------------------------------------------------------------------

  it("tabs created under one scope do not appear under another scope", () => {
    const store = useTerminalTabsStore();
    store.loadScope("a", { directory: "/home", shell: "bash" });
    store.addTab("a", { directory: "/tmp", shell: "bash" });
    expect(store.tabs("a")).toHaveLength(2);

    // Scope "b" was never loaded — should have zero tabs
    expect(store.tabs("b")).toHaveLength(0);
    expect(store.activeTabId("b")).toBeNull();
    expect(store.activeTab("b")).toBeNull();
  });

  // ---------------------------------------------------------------------------
  // 9b. Per-directory isolation: same box, two directories → independent strips
  //     (this is the regression anchor for the "tabs warp across dirs" bug)
  // ---------------------------------------------------------------------------

  it("the same box with two directories keeps independent tab strips", () => {
    const store = useTerminalTabsStore();
    const alpha = scopeKey("local", "C:\\work\\alpha");
    const beta = scopeKey("local", "C:\\work\\beta");

    store.loadScope(alpha, { directory: "C:\\work\\alpha", shell: "pwsh" });
    store.addTab(alpha, { directory: "C:\\work\\alpha", shell: "pwsh" });
    store.loadScope(beta, { directory: "C:\\work\\beta", shell: "pwsh" });

    // alpha has 2 tabs, beta has its own single seed tab — no bleed-through.
    expect(store.tabs(alpha)).toHaveLength(2);
    expect(store.tabs(beta)).toHaveLength(1);
    expect(store.tabs(alpha)[0]!.sessionName).toBe("alpha");
    expect(store.tabs(beta)[0]!.sessionName).toBe("beta");
    // Distinct localStorage keys per (box, directory) scope.
    expect(localStorage.getItem("sshler.terminal_tabs." + alpha)).not.toBeNull();
    expect(localStorage.getItem("sshler.terminal_tabs." + beta)).not.toBeNull();
    expect(alpha).not.toBe(beta);
  });

  // ---------------------------------------------------------------------------
  // 10. Persistence round-trip: new store instance reads back from localStorage
  // ---------------------------------------------------------------------------

  it("persists tabs to localStorage and restores them in a new store instance", () => {
    // First store instance: make mutations
    const store1 = useTerminalTabsStore();
    store1.loadScope("a", { directory: "/home", shell: "bash" });
    const tab2 = store1.addTab("a", { directory: "/etc", shell: "zsh" });
    // tab2 is now active
    const savedTabs = store1.tabs("a").map((t) => ({ id: t.id, sessionName: t.sessionName }));
    const savedActiveId = store1.activeTabId("a")!;
    expect(savedTabs).toHaveLength(2);
    expect(savedActiveId).toBe(tab2.id);

    // Second store instance on a fresh pinia (simulates page reload)
    setActivePinia(createPinia());
    const store2 = useTerminalTabsStore();
    // loadScope with the seed — should restore from localStorage, not reset to seed
    store2.loadScope("a", { directory: "/home", shell: "bash" });

    const restoredTabs = store2.tabs("a");
    expect(restoredTabs).toHaveLength(2);
    expect(restoredTabs[0]!.id).toBe(savedTabs[0]!.id);
    expect(restoredTabs[0]!.sessionName).toBe(savedTabs[0]!.sessionName);
    expect(restoredTabs[1]!.id).toBe(savedTabs[1]!.id);
    expect(restoredTabs[1]!.sessionName).toBe(savedTabs[1]!.sessionName);
    expect(store2.activeTabId("a")).toBe(savedActiveId);
  });

  // ---------------------------------------------------------------------------
  // 11. Corrupt localStorage falls back gracefully to a fresh default tab
  // ---------------------------------------------------------------------------

  it("falls back to a fresh default tab when localStorage is corrupt JSON", () => {
    localStorage.setItem("sshler.terminal_tabs.a", "{not json");
    const store = useTerminalTabsStore();
    // Must not throw; must produce exactly 1 default tab
    expect(() => store.loadScope("a", SEED)).not.toThrow();
    const tabList = store.tabs("a");
    expect(tabList).toHaveLength(1);
    expect(tabList[0]!.directory).toBe("/srv/app");
    expect(tabList[0]!.shell).toBe("bash");
    expect(tabList[0]!.sessionName).toBe("app");
  });

  // ---------------------------------------------------------------------------
  // 12. generateSessionName and lastPathSegment helper functions
  // ---------------------------------------------------------------------------

  describe("generateSessionName", () => {
    it("maps '~' to 'home'", () => {
      expect(generateSessionName("~")).toBe("home");
    });

    it("maps '' (empty string) to 'home'", () => {
      expect(generateSessionName("")).toBe("home");
    });

    it("replaces non-alphanumeric (except dash) and trims: '/srv/My App!'", () => {
      // lastPathSegment('/srv/My App!') → 'My App!'
      // replace(/[^a-zA-Z0-9-]/g, '_')  → 'My_App_'
      // strip leading/trailing _/-      → 'My_App'
      expect(generateSessionName("/srv/My App!")).toBe("My_App");
    });

    it("keeps dashes to match the ts CLI: '/home/gabu/projects/my-app'", () => {
      // ts sanitizes only [.:] → _, so 'my-app' must stay 'my-app' (not 'my_app')
      // or sshler and `ts here` would land on different tmux sessions.
      expect(generateSessionName("/home/gabu/projects/my-app")).toBe("my-app");
    });

    it("collapses dots like ts does: '/srv/v2.0'", () => {
      // ts: ${name//[.:]/_} → 'v2_0'; sshler must agree.
      expect(generateSessionName("/srv/v2.0")).toBe("v2_0");
    });

    it("leaves a plain alphanumeric segment untouched: 'nihongoler'", () => {
      expect(generateSessionName("/home/gabu/projects/nihongoler")).toBe("nihongoler");
    });

    it("returns 'root' for a path whose last segment is all non-alphanumeric", () => {
      // lastPathSegment('/...') → '...' → replace → '___' → strip → '' → 'root'
      expect(generateSessionName("/...")).toBe("root");
    });
  });

  describe("scopeKey", () => {
    it("combines box and directory into one key", () => {
      expect(scopeKey("local", "C:\\work\\alpha")).toBe("local::C:\\work\\alpha");
    });

    it("maps an empty directory to the home scope", () => {
      expect(scopeKey("local", "")).toBe("local::~");
    });

    it("produces different keys for the same box in different dirs", () => {
      expect(scopeKey("local", "/a")).not.toBe(scopeKey("local", "/b"));
    });

    it("produces different keys for the same dir on different boxes", () => {
      expect(scopeKey("box1", "/a")).not.toBe(scopeKey("box2", "/a"));
    });
  });

  describe("lastPathSegment", () => {
    it("extracts the last POSIX segment", () => {
      expect(lastPathSegment("/a/b/c")).toBe("c");
    });

    it("extracts the last Windows segment", () => {
      expect(lastPathSegment("C:\\x\\y")).toBe("y");
    });

    it("handles a bare name with no separators", () => {
      expect(lastPathSegment("home")).toBe("home");
    });

    it("returns empty string for an empty path", () => {
      expect(lastPathSegment("")).toBe("");
    });
  });

  // ---------------------------------------------------------------------------
  // 13. closeTab on a MIDDLE tab activates the correct neighbour
  //     idx=1, next=[t0,t2], next[Math.min(1,1)] = t2
  // ---------------------------------------------------------------------------

  it("closeTab on a middle active tab activates next[min(idx,len-1)] which is t2", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    const t0 = store.tabs(BOX)[0]!;
    const t1 = store.addTab(BOX, { directory: "/var/log", shell: "bash" });
    const t2 = store.addTab(BOX, { directory: "/etc", shell: "bash" });

    // Make t1 the active tab before closing
    store.activateTab(BOX, t1.id);
    expect(store.activeTabId(BOX)).toBe(t1.id);

    store.closeTab(BOX, t1.id);

    const remaining = store.tabs(BOX);
    expect(remaining).toHaveLength(2);
    expect(remaining[0]!.id).toBe(t0.id);
    expect(remaining[1]!.id).toBe(t2.id);
    // idx of t1 was 1, next=[t0,t2], next[Math.min(1,1)] = next[1] = t2
    expect(store.activeTabId(BOX)).toBe(t2.id);
  });

  // ---------------------------------------------------------------------------
  // 14. closeTab on the FIRST tab while active activates the new index 0 (t1)
  //     idx=0, next=[t1,t2], next[Math.min(0,1)] = t1
  // ---------------------------------------------------------------------------

  it("closeTab on the first active tab activates the new index-0 tab (t1)", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    const t0 = store.tabs(BOX)[0]!;
    const t1 = store.addTab(BOX, { directory: "/var/log", shell: "bash" });
    const t2 = store.addTab(BOX, { directory: "/etc", shell: "bash" });

    // Make t0 active
    store.activateTab(BOX, t0.id);
    expect(store.activeTabId(BOX)).toBe(t0.id);

    store.closeTab(BOX, t0.id);

    const remaining = store.tabs(BOX);
    expect(remaining).toHaveLength(2);
    expect(remaining[0]!.id).toBe(t1.id);
    expect(remaining[1]!.id).toBe(t2.id);
    // idx was 0, next=[t1,t2], next[Math.min(0,1)] = next[0] = t1
    expect(store.activeTabId(BOX)).toBe(t1.id);
  });

  // ---------------------------------------------------------------------------
  // 15. closeTab on a NON-active tab leaves activeTabId unchanged
  // ---------------------------------------------------------------------------

  it("closeTab on a non-active tab does not change the active tab", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    const t0 = store.tabs(BOX)[0]!;
    const t1 = store.addTab(BOX, { directory: "/var/log", shell: "bash" });
    const t2 = store.addTab(BOX, { directory: "/etc", shell: "bash" });

    // Make t2 active
    store.activateTab(BOX, t2.id);
    expect(store.activeTabId(BOX)).toBe(t2.id);

    // Close the non-active middle tab
    store.closeTab(BOX, t1.id);

    const remaining = store.tabs(BOX);
    expect(remaining).toHaveLength(2);
    expect(remaining[0]!.id).toBe(t0.id);
    expect(remaining[1]!.id).toBe(t2.id);
    // Active must be unchanged — still t2
    expect(store.activeTabId(BOX)).toBe(t2.id);
  });

  // ---------------------------------------------------------------------------
  // 16. activateTab: direct activation and no-op for nonexistent id
  // ---------------------------------------------------------------------------

  it("activateTab switches to the target tab and is a no-op for unknown ids", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    const t0 = store.tabs(BOX)[0]!;
    const t1 = store.addTab(BOX, { directory: "/var/log", shell: "bash" });
    // After addTab, t1 is active
    expect(store.activeTabId(BOX)).toBe(t1.id);

    // Activate t0 explicitly
    store.activateTab(BOX, t0.id);
    expect(store.activeTabId(BOX)).toBe(t0.id);

    // Activating a nonexistent id is a no-op — active stays t0
    store.activateTab(BOX, "nope");
    expect(store.activeTabId(BOX)).toBe(t0.id);
  });

  // ---------------------------------------------------------------------------
  // 17. renameTab with empty or whitespace-only label leaves label unchanged
  // ---------------------------------------------------------------------------

  it("renameTab with empty string leaves label unchanged", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    const tab = store.tabs(BOX)[0]!;
    const originalLabel = tab.label;

    store.renameTab(BOX, tab.id, "");

    expect(store.tabs(BOX)[0]!.label).toBe(originalLabel);
  });

  it("renameTab with whitespace-only string leaves label unchanged", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    const tab = store.tabs(BOX)[0]!;
    const originalLabel = tab.label;

    store.renameTab(BOX, tab.id, "   ");

    expect(store.tabs(BOX)[0]!.label).toBe(originalLabel);
  });

  // ---------------------------------------------------------------------------
  // 18. Persistence round-trip preserves directory and shell (not just id/name)
  // ---------------------------------------------------------------------------

  it("persists and restores directory and shell fields in addition to id and sessionName", () => {
    const store1 = useTerminalTabsStore();
    store1.loadScope("b", { directory: "/srv/web", shell: "zsh" });
    store1.addTab("b", { directory: "/opt/data", shell: "fish" });

    const saved = store1.tabs("b").map((t) => ({
      id: t.id,
      sessionName: t.sessionName,
      directory: t.directory,
      shell: t.shell,
    }));
    expect(saved).toHaveLength(2);
    expect(saved[0]!.directory).toBe("/srv/web");
    expect(saved[0]!.shell).toBe("zsh");
    expect(saved[1]!.directory).toBe("/opt/data");
    expect(saved[1]!.shell).toBe("fish");

    // Simulate page reload with a fresh pinia
    setActivePinia(createPinia());
    const store2 = useTerminalTabsStore();
    store2.loadScope("b", { directory: "/srv/web", shell: "zsh" });

    const restored = store2.tabs("b");
    expect(restored).toHaveLength(2);
    expect(restored[0]!.id).toBe(saved[0]!.id);
    expect(restored[0]!.directory).toBe("/srv/web");
    expect(restored[0]!.shell).toBe("zsh");
    expect(restored[1]!.id).toBe(saved[1]!.id);
    expect(restored[1]!.directory).toBe("/opt/data");
    expect(restored[1]!.shell).toBe("fish");
  });

  // ---------------------------------------------------------------------------
  // 19. loadScope idempotency: second call restores from storage (does not reset)
  // ---------------------------------------------------------------------------

  it("loadScope called again after addTab restores from storage (idempotent)", () => {
    const store = useTerminalTabsStore();
    store.loadScope(BOX, SEED);
    // Add a second tab — now 2 tabs are persisted
    const t1 = store.addTab(BOX, { directory: "/home/user", shell: "zsh" });
    expect(store.tabs(BOX)).toHaveLength(2);

    // Call loadScope again with the original seed
    store.loadScope(BOX, SEED);

    // Must restore from storage (2 tabs), NOT reset to 1 seed tab
    const restored = store.tabs(BOX);
    expect(restored).toHaveLength(2);
    expect(restored[1]!.id).toBe(t1.id);
    expect(restored[1]!.directory).toBe("/home/user");
    expect(restored[1]!.shell).toBe("zsh");
  });
});
