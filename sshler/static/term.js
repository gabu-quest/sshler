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

    const dirLabel = root.dataset.dirLabel || "";
    if (dirLabel) {
      document.title = `${dirLabel} — sshler`;
    }

    document.body.classList.add("term-view");
    if (typeof window.sshlerSetFavicon === "function") {
      window.sshlerSetFavicon("terminal");
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
    });
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(document.getElementById("term"));

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
