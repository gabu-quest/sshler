(function () {
  function getToken() {
    if (window.sshlerToken) {
      return window.sshlerToken;
    }
    const tokenMeta = document.querySelector('meta[name="sshler-token"]');
    return tokenMeta ? tokenMeta.getAttribute("content") || "" : "";
  }

  function setupCommandButtons(ws) {
    const commandMap = {
      "scroll-mode": { type: "send", payload: "\u0002[" },
      escape: { type: "send", payload: "\u001b" },
      "ctrl-t": { type: "send", payload: "\u0014" },
      "ctrl-c": { type: "send", payload: "\u0003" },
      "split-h": { type: "send", payload: "\u0002%" },
      "split-v": { type: "send", payload: "\u0002\"" },
      "new-window": { type: "send", payload: "\u0002c" },
      "rename-window": { type: "operation", op: "rename-window" },
      "kill-pane": { type: "send", payload: "\u0002x" },
      next: { type: "send", payload: "\u0002n" },
      prev: { type: "send", payload: "\u0002p" },
      detach: { type: "send", payload: "\u0002d" },
    };

    document
      .querySelectorAll(".term-toolbar [data-command]")
      .forEach((button) => {
        button.addEventListener("click", () => {
          const command = button.dataset.command;
          const config = commandMap[command];
          if (!config) {
            return;
          }
          if (config.type === "send") {
            ws.send(
              JSON.stringify({ op: "send", data: config.payload }),
            );
          } else if (config.type === "operation" && config.op === "rename-window") {
            const newName = prompt("Rename window to:");
            if (newName) {
              ws.send(
                JSON.stringify({ op: "rename-window", target: newName }),
              );
            }
          }
        });
      });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector("[data-term-root]");
    if (!root) {
      return;
    }

    const transport = root.dataset.transport || "ssh";
    const isLocal = transport === "local";

    const dirLabel = root.dataset.dirLabel || "";
    if (dirLabel) {
      document.title = `${dirLabel} — sshler`;
    }

    document.body.classList.add("term-view");
    if (typeof window.sshlerSetFavicon === "function") {
      window.sshlerSetFavicon(isLocal ? "terminal-local" : "terminal");
    }
    window.addEventListener("beforeunload", () => {
      document.body.classList.remove("term-view");
      if (typeof window.sshlerSetFavicon === "function") {
        window.sshlerSetFavicon("default");
      }
    });

    const term = new Terminal({
      cursorBlink: true,
      convertEol: true,
      scrollback: 10000,
      fastScrollModifier: "shift",
      fastScrollSensitivity: 5,
      bellStyle: "sound",
    });
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(document.getElementById("term"));

    const notifyContext = {
      host: root.dataset.host || root.dataset.boxName || "",
      session: root.dataset.session || "default",
      dirLabel,
    };

    let pendingTitleRestore = null;
    let notificationPermissionRequested = false;

    function decodeSegment(value) {
      if (!value) {
        return "";
      }
      try {
        return decodeURIComponent(value.replace(/\+/g, "%20"));
      } catch (err) {
        return value;
      }
    }

    function restoreTitle() {
      if (pendingTitleRestore) {
        pendingTitleRestore();
        pendingTitleRestore = null;
      }
    }

    function emphasizeTitle(message) {
      if (!document.hidden || pendingTitleRestore) {
        return;
      }
      const previousTitle = document.title;
      document.title = `★ ${message}`;
      pendingTitleRestore = () => {
        document.title = previousTitle;
      };
      document.addEventListener(
        "visibilitychange",
        () => {
          if (!document.hidden) {
            restoreTitle();
          }
        },
        { once: true },
      );
    }

    function maybeShowSystemNotification(title, body, options) {
      if (typeof Notification === "undefined") {
        return;
      }

      const payload = {
        body: body || "",
        tag: options?.tag,
        renotify: true,
      };

      if (Notification.permission === "granted") {
        new Notification(title, payload);
        return;
      }

      if (Notification.permission === "denied" || notificationPermissionRequested) {
        return;
      }

      notificationPermissionRequested = true;
      Notification.requestPermission().then((permission) => {
        if (permission === "granted") {
          new Notification(title, payload);
        }
      }).finally(() => {
        notificationPermissionRequested = false;
      });
    }

    function notifyUser(title, body, options) {
      const opts = options || {};
      const toastMessage = body || title;
      const shouldToast = opts.forceToast || !document.hidden;
      if (shouldToast && typeof window.sshlerShowToast === "function") {
        window.sshlerShowToast(toastMessage, opts.level || "info");
      }

      if (document.hidden || opts.alwaysNotify) {
        emphasizeTitle(title);
        maybeShowSystemNotification(title, body || toastMessage, opts);
      }
    }

    function registerOscHandlers() {
      if (typeof term.registerOscHandler !== "function") {
        return;
      }

      term.registerOscHandler(777, (data) => {
        const payload = (data || "").trim();
        if (!payload || !payload.toLowerCase().startsWith("notify=")) {
          return false;
        }

        let message = payload.slice(7);
        let title = notifyContext.dirLabel || notifyContext.host || "Terminal";
        let level = "info";

        if (!message) {
          notifyUser(title, "", { forceToast: true, alwaysNotify: true });
          return true;
        }

        const trimmed = message.trim();
        if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
          try {
            const parsed = JSON.parse(trimmed);
            if (parsed.title) {
              title = String(parsed.title);
            }
            if (parsed.message || parsed.body) {
              message = String(parsed.message || parsed.body);
            }
            if (parsed.level) {
              level = String(parsed.level);
            }
          } catch (err) {
            // Fall back to original message when JSON parsing fails
          }
        } else {
          const segments = trimmed.split("|", 2);
          if (segments.length === 2) {
            title = decodeSegment(segments[0]) || title;
            message = decodeSegment(segments[1]);
          } else {
            message = decodeSegment(trimmed);
          }
        }

        notifyUser(title, message, {
          level,
          forceToast: true,
          alwaysNotify: true,
          tag: `notify-${notifyContext.host || "terminal"}-${notifyContext.session}`,
        });

        return true;
      });
    }

    registerOscHandlers();

    // Fit immediately to get proper dimensions before creating WebSocket
    // Use triple requestAnimationFrame to ensure layout is fully settled
    let ws;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          // Now the layout should be fully calculated
          fitAddon.fit();

          const url = new URL(window.location.href);
          const host = url.searchParams.get("host") || root.dataset.host || "";
          const directory = url.searchParams.get("dir") || root.dataset.directory || "/";
          const session =
            url.searchParams.get("session") || root.dataset.session || "default";
          const wsProto = location.protocol === "https:" ? "wss://" : "ws://";
          const token = getToken();

          notifyContext.host = host || notifyContext.host;
          notifyContext.session = session || notifyContext.session;
          if (directory) {
            const parts = directory.replace(/\/?$/, "").split("/");
            notifyContext.dirLabel = parts.pop() || "/";
          }

          // Now use the fitted dimensions
          const wsUrl =
            wsProto +
            location.host +
            `/ws/term?host=${encodeURIComponent(host)}&dir=${encodeURIComponent(directory)}&session=${encodeURIComponent(session)}&cols=${term.cols}&rows=${term.rows}&token=${encodeURIComponent(token)}`;

          ws = new WebSocket(wsUrl);
          ws.binaryType = "arraybuffer";

          setupWebSocket(ws, term, fitAddon);
        });
      });
    });

    function setupWebSocket(ws, term, fitAddon) {
      const encoder = new TextEncoder();
      const termToolbar = document.getElementById("term-toolbar");
      const termWrapper = document.getElementById("term-wrapper");
      const filePanel = document.getElementById("file-panel");
      const fileBrowser = document.getElementById("file-browser");
      const tabsContainer = document.getElementById("tmux-tabs");

      let filePanelActive = false;
      let filePanelLoaded = false;
      let fileTabButton = null;
      let latestWindows = [];

      function sendResize() {
        // Use requestAnimationFrame to ensure DOM is updated before fitting
        requestAnimationFrame(() => {
          fitAddon.fit();
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(
              JSON.stringify({ op: "resize", cols: term.cols, rows: term.rows }),
            );
          }
        });
      }

    let lastBellTimestamp = 0;
    term.onBell(() => {
      if (!document.hidden) {
        return;
      }
      const now = Date.now();
      if (now - lastBellTimestamp < 1500) {
        return;
      }
      lastBellTimestamp = now;
      const hostLabel = notifyContext.host || "Terminal";
      const message = notifyContext.session
        ? `Session ${notifyContext.session} sent a bell`
        : "Terminal bell";
      notifyUser(`${hostLabel} — Bell`, message, {
        forceToast: false,
        level: "info",
        tag: `bell-${notifyContext.host || "host"}-${notifyContext.session}`,
      });
    });

    function activateTerminalView() {
      if (!filePanelActive) {
        return;
      }
      filePanelActive = false;
      termToolbar.classList.remove("hidden");
      termWrapper.classList.remove("hidden");
      filePanel.classList.add("hidden");
      if (fileTabButton) {
        fileTabButton.classList.remove("active");
      }
      // Ensure terminal refits after panel visibility changes
      requestAnimationFrame(() => {
        fitAddon.fit();
      });
    }

    function activateFileView() {
      if (filePanelActive) {
        return;
      }
      filePanelActive = true;
      termToolbar.classList.add("hidden");
      termWrapper.classList.add("hidden");
      filePanel.classList.remove("hidden");
      if (!filePanelLoaded && window.htmx) {
        window.htmx.trigger(fileBrowser, "revealed");
        filePanelLoaded = true;
      }
      if (fileTabButton) {
        fileTabButton.classList.add("active");
      }
    }

    function renderTabs(windows) {
      latestWindows = windows || [];
      if (!tabsContainer) {
        return;
      }
      tabsContainer.innerHTML = "";

      latestWindows.forEach((windowInfo) => {
        const tab = document.createElement("button");
        const isActive = windowInfo.active && !filePanelActive;
        tab.className = "tmux-tab" + (isActive ? " active" : "");
        const name = windowInfo.name || `#${windowInfo.index}`;
        tab.textContent = `${windowInfo.index}: ${name}`;
        tab.addEventListener("click", () => {
          activateTerminalView();
          ws.send(
            JSON.stringify({
              op: "select-window",
              target: windowInfo.index,
            }),
          );
        });
        tabsContainer.appendChild(tab);
      });

      const separator = document.createElement("span");
      separator.className = "tmux-separator";
      separator.textContent = "|";
      tabsContainer.appendChild(separator);

      fileTabButton = document.createElement("button");
      fileTabButton.className = "tmux-tab" + (filePanelActive ? " active" : "");
      fileTabButton.setAttribute("data-i18n", "term.files");
      fileTabButton.textContent = "Files";
      fileTabButton.addEventListener("click", () => {
        if (filePanelActive) {
          activateTerminalView();
        } else {
          activateFileView();
        }
        renderTabs(latestWindows);
      });
      tabsContainer.appendChild(fileTabButton);

      // Translate the Files button after creating it
      if (typeof window.sshlerTranslate === "function") {
        window.sshlerTranslate();
      }
    }

    ws.onopen = () => {
      term.focus();
    };

    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        try {
          const message = JSON.parse(event.data);
          if (message.op === "windows") {
            renderTabs(message.windows);
            return;
          }
        } catch (err) {
          term.write(event.data);
          return;
        }
        term.write(event.data);
      } else if (event.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(event.data));
      }
    };

    ws.onclose = () => {
      term.write("\r\n\u001b[31m[Connection closed — refresh to reconnect]\u001b[0m\r\n");
      restoreTitle();
    };

    term.onData((data) => {
      ws.send(encoder.encode(data));
    });

    term.attachCustomKeyEventHandler((ev) => {
      if (ev.ctrlKey && ev.key && ev.key.toLowerCase() === "t") {
        ws.send(JSON.stringify({ op: "send", data: "\u0014" }));
        return false;
      }
      return true;
    });

    window.addEventListener("resize", sendResize);
    window.addEventListener("focus", () => term.focus());
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        sendResize();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "b") {
        event.preventDefault();
        if (filePanelActive) {
          activateTerminalView();
        } else {
          activateFileView();
        }
        renderTabs(latestWindows);
      }
    });

    const termElement = document.getElementById("term");

    termElement.addEventListener("contextmenu", async (event) => {
      event.preventDefault();
      const selection = term.getSelection();
      if (selection) {
        try {
          await navigator.clipboard.writeText(selection);
          term.clearSelection();
        } catch (err) {
          console.warn("Clipboard copy failed", err);
        }
        return;
      }
      try {
        const text = await navigator.clipboard.readText();
        if (text) {
          ws.send(JSON.stringify({ op: "send", data: text }));
        }
      } catch (err) {
        console.warn("Clipboard paste failed", err);
      }
    });

      setupCommandButtons(ws);
      renderTabs([]);
    }
  });
})();
