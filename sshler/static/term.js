(function () {
  function getToken() {
    if (window.sshlerToken) {
      return window.sshlerToken;
    }
    const tokenMeta = document.querySelector('meta[name="sshler-token"]');
    return tokenMeta ? tokenMeta.getAttribute("content") || "" : "";
  }

  function showScrollModeIndicator() {
    // Check if indicator already exists
    let indicator = document.querySelector('.scroll-mode-indicator');
    if (!indicator) {
      indicator = document.createElement('div');
      indicator.className = 'scroll-mode-indicator';
      indicator.innerHTML = `
        <span class="scroll-icon">📜</span>
        <div class="scroll-info">
          <div class="scroll-title">SCROLL MODE</div>
          <div class="scroll-hint">↑↓ navigate • PgUp/PgDn page • / search • q quit</div>
        </div>
      `;
      document.body.appendChild(indicator);

      // Add styles if not already present
      if (!document.querySelector('#scroll-mode-styles')) {
        const style = document.createElement('style');
        style.id = 'scroll-mode-styles';
        style.textContent = `
          .scroll-mode-indicator {
            position: fixed;
            top: 20px;
            right: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 12px 20px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
            display: flex;
            align-items: center;
            gap: 12px;
            z-index: 10000;
            animation: slideIn 0.3s ease-out;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          }

          @keyframes slideIn {
            from {
              transform: translateX(100%);
              opacity: 0;
            }
            to {
              transform: translateX(0);
              opacity: 1;
            }
          }

          .scroll-icon {
            font-size: 24px;
          }

          .scroll-info {
            display: flex;
            flex-direction: column;
            gap: 4px;
          }

          .scroll-title {
            font-weight: 700;
            font-size: 14px;
            letter-spacing: 0.5px;
          }

          .scroll-hint {
            font-size: 12px;
            opacity: 0.9;
            font-weight: 400;
          }

          .scroll-hint kbd {
            background: rgba(255, 255, 255, 0.2);
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 11px;
          }
        `;
        document.head.appendChild(style);
      }
    }
    indicator.style.display = 'flex';
  }

  function hideScrollModeIndicator() {
    const indicator = document.querySelector('.scroll-mode-indicator');
    if (indicator) {
      indicator.style.display = 'none';
    }
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

            // Show scroll mode indicator when entering scroll mode
            if (command === "scroll-mode") {
              showScrollModeIndicator();
              // Hide after a delay (user presses 'q' to exit, so we auto-hide after 30s)
              setTimeout(hideScrollModeIndicator, 30000);
            }
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

          let reconnectAttempts = 0;
          const MAX_RECONNECT_ATTEMPTS = 5;
          const RECONNECT_BASE_DELAY = 1000; // 1 second
          let reconnectTimeout = null;
          let intentionalDisconnect = false;

          function createWebSocket() {
            ws = new WebSocket(wsUrl);
            ws.binaryType = "arraybuffer";
            return ws;
          }

          function attemptReconnect() {
            if (intentionalDisconnect || reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
              return;
            }

            reconnectAttempts++;
            const delay = RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts - 1); // Exponential backoff

            term.write(`\r\n\u001b[33m[Connection lost. Reconnecting in ${delay / 1000}s... (${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})]\u001b[0m\r\n`);

            reconnectTimeout = setTimeout(() => {
              term.write(`\u001b[33m[Attempting to reconnect...]\u001b[0m\r\n`);
              try {
                const newWs = createWebSocket();
                setupWebSocket(newWs, term, fitAddon, true);
              } catch (error) {
                term.write(`\u001b[31m[Reconnection failed: ${error.message}]\u001b[0m\r\n`);
                attemptReconnect();
              }
            }, delay);
          }

          ws = createWebSocket();
          setupWebSocket(ws, term, fitAddon, false);
        });
      });
    });

    function setupWebSocket(ws, term, fitAddon, isReconnect) {
      isReconnect = isReconnect || false;
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

      // Throttled resize to prevent flooding WebSocket
      let resizeTimeout = null;
      let lastResizeTime = 0;
      const RESIZE_THROTTLE_MS = 100; // Minimum 100ms between resize messages

      function sendResize() {
        const now = Date.now();
        const timeSinceLastResize = now - lastResizeTime;

        // Clear any pending resize
        if (resizeTimeout) {
          clearTimeout(resizeTimeout);
          resizeTimeout = null;
        }

        // If enough time has passed, resize immediately
        if (timeSinceLastResize >= RESIZE_THROTTLE_MS) {
          performResize();
          lastResizeTime = now;
        } else {
          // Otherwise, schedule resize after throttle period
          const delay = RESIZE_THROTTLE_MS - timeSinceLastResize;
          resizeTimeout = setTimeout(() => {
            performResize();
            lastResizeTime = Date.now();
            resizeTimeout = null;
          }, delay);
        }
      }

      function performResize() {
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
      if (isReconnect) {
        // Successfully reconnected
        reconnectAttempts = 0;
        term.write(`\r\n\u001b[32m[✓ Reconnected successfully!]\u001b[0m\r\n`);
        // Send resize to ensure terminal is properly sized
        performResize();
      }
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

    ws.onerror = (error) => {
      console.error("WebSocket error:", error);
    };

    ws.onclose = (event) => {
      // Check if this was an intentional disconnect (user detached)
      const wasClean = event.wasClean;
      const code = event.code;

      // Code 1000 = normal closure, 1001 = going away (refresh)
      if (wasClean || code === 1000 || code === 1001) {
        intentionalDisconnect = true;
        term.write("\r\n\u001b[33m[Connection closed]\u001b[0m\r\n");
      } else {
        // Unexpected disconnect, try to reconnect
        term.write(`\r\n\u001b[31m[Connection lost unexpectedly (code: ${code})]\u001b[0m\r\n`);
        attemptReconnect();
      }
      restoreTitle();
    };

    term.onData((data) => {
      ws.send(encoder.encode(data));
    });

    term.attachCustomKeyEventHandler((ev) => {
      // Ctrl+T
      if (ev.ctrlKey && ev.key && ev.key.toLowerCase() === "t") {
        ws.send(JSON.stringify({ op: "send", data: "\u0014" }));
        return false;
      }

      // Shift+PageUp to enter scroll mode
      if (ev.shiftKey && ev.key === "PageUp" && !ev.ctrlKey && !ev.altKey) {
        ws.send(JSON.stringify({ op: "send", data: "\u0002[" })); // Ctrl+B [
        showScrollModeIndicator();
        setTimeout(hideScrollModeIndicator, 30000);
        return false;
      }

      // Detect 'q' to hide scroll mode indicator (user exiting scroll mode)
      if (ev.key === "q" && !ev.ctrlKey && !ev.shiftKey && !ev.altKey) {
        // Hide indicator after a small delay to ensure user pressed 'q' in scroll mode
        setTimeout(hideScrollModeIndicator, 200);
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
