(function () {
  'use strict';

  class MultiSessionManager {
    constructor(boxName, initialSession, initialDirectory) {
      this.boxName = boxName;
      this.panes = [];
      this.focusedPaneIndex = 0;
      this.nextPaneId = 1;
      this.layout = 'single'; // 'single', 'hsplit', 'vsplit', 'grid'

      // Initialize with the first pane (already created by term.js)
      this.registerInitialPane(initialSession, initialDirectory);
      this.setupEventListeners();
      this.loadLayoutFromStorage();
    }

    registerInitialPane(session, directory) {
      const paneEl = document.getElementById('term-pane-0');
      if (paneEl && window.termInstance) {
        this.panes.push({
          id: 0,
          element: paneEl,
          terminal: window.termInstance,
          websocket: window.wsInstance,
          fitAddon: window.fitAddonInstance,
          session: session,
          directory: directory,
        });
      }
    }

    setupEventListeners() {
      // Session manager toggle
      const managerBtn = document.getElementById('session-manager-btn');
      const managerPanel = document.getElementById('session-manager-panel');
      const closeBtn = document.getElementById('close-session-manager');

      if (managerBtn && managerPanel) {
        managerBtn.addEventListener('click', () => {
          managerPanel.classList.toggle('hidden');
          if (!managerPanel.classList.contains('hidden')) {
            // Refresh session list
            htmx.trigger(document.body, 'sessionListRefresh');
          }
        });
      }

      if (closeBtn && managerPanel) {
        closeBtn.addEventListener('click', () => {
          managerPanel.classList.add('hidden');
        });
      }

      // Split buttons
      document.getElementById('split-horizontal-btn')?.addEventListener('click', () => {
        this.splitHorizontal();
      });

      document.getElementById('split-vertical-btn')?.addEventListener('click', () => {
        this.splitVertical();
      });

      document.getElementById('close-pane-btn')?.addEventListener('click', () => {
        this.closeCurrentPane();
      });

      // Keyboard shortcuts for pane navigation
      document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.shiftKey) {
          switch (e.key.toUpperCase()) {
            case 'H':
              e.preventDefault();
              this.focusPaneLeft();
              break;
            case 'L':
              e.preventDefault();
              this.focusPaneRight();
              break;
            case 'J':
              e.preventDefault();
              this.focusPaneDown();
              break;
            case 'K':
              e.preventDefault();
              this.focusPaneUp();
              break;
            case 'W':
              e.preventDefault();
              this.cycleNextPane();
              break;
            case 'X':
              e.preventDefault();
              this.closeCurrentPane();
              break;
          }
        }
      });

      // Click to focus panes
      document.addEventListener('click', (e) => {
        const pane = e.target.closest('.term-pane');
        if (pane) {
          const index = this.panes.findIndex(p => p.element === pane);
          if (index !== -1) {
            this.focusPane(index);
          }
        }
      });
    }

    splitHorizontal() {
      const container = document.getElementById('term-panes-container');
      if (!container) return;

      // Show session picker modal
      this.showSessionPicker((selectedSession) => {
        const newPaneId = this.nextPaneId++;
        const paneEl = this.createPaneElement(newPaneId, selectedSession.session_name);

        // Update layout
        container.classList.add('horizontal-split');
        container.appendChild(paneEl);

        // Create terminal instance for new pane
        this.initializeTerminalForPane(paneEl, selectedSession);

        // Update layout
        this.layout = this.panes.length === 2 ? 'hsplit' : 'grid';
        this.saveLayoutToStorage();
      });
    }

    splitVertical() {
      const container = document.getElementById('term-panes-container');
      if (!container) return;

      // Show session picker modal
      this.showSessionPicker((selectedSession) => {
        const newPaneId = this.nextPaneId++;
        const paneEl = this.createPaneElement(newPaneId, selectedSession.session_name);

        // Update layout
        container.classList.add('vertical-split');
        container.appendChild(paneEl);

        // Create terminal instance for new pane
        this.initializeTerminalForPane(paneEl, selectedSession);

        // Update layout
        this.layout = this.panes.length === 2 ? 'vsplit' : 'grid';
        this.saveLayoutToStorage();
      });
    }

    createPaneElement(paneId, sessionName) {
      const paneEl = document.createElement('div');
      paneEl.className = 'term-pane';
      paneEl.id = `term-pane-${paneId}`;
      paneEl.dataset.paneIndex = paneId;
      paneEl.innerHTML = `
        <div class="term-pane-header">
          <span class="term-pane-title">${sessionName}</span>
          <span class="term-pane-focus-indicator">●</span>
          <button class="term-pane-close" data-pane-id="${paneId}">×</button>
        </div>
        <div id="term-${paneId}" class="term"></div>
      `;

      // Add close button handler
      paneEl.querySelector('.term-pane-close').addEventListener('click', (e) => {
        e.stopPropagation();
        const index = this.panes.findIndex(p => p.id === paneId);
        if (index !== -1) {
          this.closePane(index);
        }
      });

      return paneEl;
    }

    initializeTerminalForPane(paneEl, sessionInfo) {
      const paneId = parseInt(paneEl.dataset.paneIndex);
      const termContainer = paneEl.querySelector('.term');

      // Create xterm instance
      const term = new Terminal({
        cursorBlink: true,
        convertEol: true,
        scrollback: 10000,
        fastScrollModifier: 'shift',
        fastScrollSensitivity: 5,
        bellStyle: 'sound',
      });

      const fitAddon = new FitAddon.FitAddon();
      term.loadAddon(fitAddon);
      term.open(termContainer);

      // Fit terminal
      setTimeout(() => {
        fitAddon.fit();

        // Create WebSocket connection
        const wsProto = location.protocol === 'https:' ? 'wss://' : 'ws://';
        const token = window.sshlerToken || '';
        const wsUrl = `${wsProto}${location.host}/ws/term?` +
          `host=${encodeURIComponent(this.boxName)}` +
          `&dir=${encodeURIComponent(sessionInfo.working_directory)}` +
          `&session=${encodeURIComponent(sessionInfo.session_name)}` +
          `&cols=${term.cols}&rows=${term.rows}` +
          `&token=${encodeURIComponent(token)}`;

        const ws = new WebSocket(wsUrl);
        ws.binaryType = 'arraybuffer';

        // Setup WebSocket handlers
        ws.onopen = () => {
          term.focus();
        };

        ws.onmessage = (event) => {
          if (typeof event.data === 'string') {
            term.write(event.data);
          } else if (event.data instanceof ArrayBuffer) {
            term.write(new Uint8Array(event.data));
          }
        };

        ws.onclose = () => {
          term.write('\r\n\u001b[31m[Connection closed]\u001b[0m\r\n');
        };

        const encoder = new TextEncoder();
        term.onData((data) => {
          ws.send(encoder.encode(data));
        });

        // Store pane info
        this.panes.push({
          id: paneId,
          element: paneEl,
          terminal: term,
          websocket: ws,
          fitAddon: fitAddon,
          session: sessionInfo.session_name,
          directory: sessionInfo.working_directory,
        });

        // Focus new pane
        this.focusPane(this.panes.length - 1);

        // Setup resize observer
        const resizeObserver = new ResizeObserver(() => {
          fitAddon.fit();
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ op: 'resize', cols: term.cols, rows: term.rows }));
          }
        });
        resizeObserver.observe(termContainer);
      }, 100);
    }

    showSessionPicker(callback) {
      // Create modal for session selection
      const modal = document.createElement('div');
      modal.className = 'session-picker-modal';
      modal.innerHTML = `
        <div class="session-picker-content">
          <div class="session-picker-header">
            <h3>Select Session</h3>
            <button class="btn-close">×</button>
          </div>
          <div class="session-picker-body">
            <div id="session-picker-list"
                 hx-get="/box/${this.boxName}/sessions?active_only=false&limit=20"
                 hx-trigger="load"
                 hx-swap="innerHTML">
              Loading sessions...
            </div>
          </div>
        </div>
      `;

      document.body.appendChild(modal);

      // Close button
      modal.querySelector('.btn-close').addEventListener('click', () => {
        modal.remove();
      });

      // Wait for HTMX to load sessions, then add click handlers
      setTimeout(() => {
        const sessionCards = modal.querySelectorAll('.session-card');
        sessionCards.forEach((card) => {
          const sessionData = {
            id: card.dataset.sessionId,
            session_name: card.dataset.sessionName,
            working_directory: card.dataset.workingDirectory || '/',
          };

          card.style.cursor = 'pointer';
          card.addEventListener('click', () => {
            callback(sessionData);
            modal.remove();
          });
        });

        // If no sessions, add option to create new one
        if (sessionCards.length === 0) {
          const newSessionBtn = document.createElement('button');
          newSessionBtn.className = 'btn primary';
          newSessionBtn.textContent = 'Create New Session';
          newSessionBtn.addEventListener('click', () => {
            const sessionName = prompt('Enter session name:');
            const directory = prompt('Enter working directory:', '/');
            if (sessionName && directory) {
              callback({
                session_name: sessionName,
                working_directory: directory,
              });
              modal.remove();
            }
          });
          modal.querySelector('.session-picker-body').appendChild(newSessionBtn);
        }
      }, 500);

      // Process HTMX content
      if (window.htmx) {
        window.htmx.process(modal);
      }
    }

    closeCurrentPane() {
      if (this.panes.length <= 1) {
        alert('Cannot close the last pane');
        return;
      }
      this.closePane(this.focusedPaneIndex);
    }

    closePane(index) {
      if (index < 0 || index >= this.panes.length || this.panes.length <= 1) {
        return;
      }

      const pane = this.panes[index];

      // Close WebSocket
      if (pane.websocket) {
        pane.websocket.close();
      }

      // Dispose terminal
      if (pane.terminal) {
        pane.terminal.dispose();
      }

      // Remove element
      pane.element.remove();

      // Remove from array
      this.panes.splice(index, 1);

      // Update focus
      if (this.focusedPaneIndex >= this.panes.length) {
        this.focusedPaneIndex = this.panes.length - 1;
      }
      this.focusPane(this.focusedPaneIndex);

      // Update layout
      const container = document.getElementById('term-panes-container');
      if (this.panes.length === 1) {
        container.className = 'term-panes-container';
        this.layout = 'single';
      }

      this.saveLayoutToStorage();
    }

    focusPane(index) {
      if (index < 0 || index >= this.panes.length) {
        return;
      }

      // Remove focus from all panes
      this.panes.forEach((p) => {
        p.element.classList.remove('focused');
      });

      // Focus the selected pane
      this.panes[index].element.classList.add('focused');
      this.panes[index].terminal.focus();
      this.focusedPaneIndex = index;
    }

    focusPaneLeft() {
      // Find pane to the left (simplified - just cycle backwards)
      const newIndex = this.focusedPaneIndex > 0 ? this.focusedPaneIndex - 1 : this.panes.length - 1;
      this.focusPane(newIndex);
    }

    focusPaneRight() {
      // Find pane to the right (simplified - just cycle forwards)
      const newIndex = (this.focusedPaneIndex + 1) % this.panes.length;
      this.focusPane(newIndex);
    }

    focusPaneUp() {
      // For vertical split, focus pane above
      this.focusPaneLeft();
    }

    focusPaneDown() {
      // For vertical split, focus pane below
      this.focusPaneRight();
    }

    cycleNextPane() {
      const newIndex = (this.focusedPaneIndex + 1) % this.panes.length;
      this.focusPane(newIndex);
    }

    saveLayoutToStorage() {
      const layout = {
        type: this.layout,
        panes: this.panes.map(p => ({
          id: p.id,
          session: p.session,
          directory: p.directory,
        })),
        focusedIndex: this.focusedPaneIndex,
      };
      localStorage.setItem(`terminal_layout_${this.boxName}`, JSON.stringify(layout));
    }

    loadLayoutFromStorage() {
      const saved = localStorage.getItem(`terminal_layout_${this.boxName}`);
      if (!saved) return;

      try {
        const layout = JSON.parse(saved);

        // Only restore if we have more than one pane saved
        if (!layout.panes || layout.panes.length <= 1) return;

        // Show restore prompt
        this.showRestorePrompt(layout);
      } catch (error) {
        console.error('Failed to load layout:', error);
      }
    }

    showRestorePrompt(layout) {
      const prompt = document.createElement('div');
      prompt.className = 'session-restore-prompt';
      prompt.innerHTML = `
        <div class="session-restore-content">
          <h3>Restore Previous Session?</h3>
          <p>You had ${layout.panes.length} terminal panes open. Restore your workspace?</p>
          <div class="session-restore-actions">
            <button class="btn primary" id="restore-yes">Restore</button>
            <button class="btn secondary" id="restore-no">Start Fresh</button>
          </div>
        </div>
      `;

      document.body.appendChild(prompt);

      document.getElementById('restore-yes')?.addEventListener('click', () => {
        this.restoreLayout(layout);
        prompt.remove();
      });

      document.getElementById('restore-no')?.addEventListener('click', () => {
        localStorage.removeItem(`terminal_layout_${this.boxName}`);
        prompt.remove();
      });

      // Auto-dismiss after 10 seconds with default action (don't restore)
      setTimeout(() => {
        if (document.body.contains(prompt)) {
          prompt.remove();
        }
      }, 10000);
    }

    async restoreLayout(layout) {
      const container = document.getElementById('term-panes-container');
      if (!container) return;

      // Restore layout type
      this.layout = layout.type || 'single';

      // Apply layout classes
      container.classList.remove('horizontal-split', 'vertical-split');
      if (this.layout === 'hsplit' || this.layout === 'grid') {
        container.classList.add('horizontal-split');
      }
      if (this.layout === 'vsplit' || this.layout === 'grid') {
        container.classList.add('vertical-split');
      }

      // Restore panes (skip first one as it already exists)
      for (let i = 1; i < layout.panes.length; i++) {
        const savedPane = layout.panes[i];
        await this.restorePane(savedPane);
      }

      // Restore focus
      if (layout.focusedIndex !== undefined && layout.focusedIndex < this.panes.length) {
        this.focusPane(layout.focusedIndex);
      }
    }

    async restorePane(savedPane) {
      return new Promise((resolve) => {
        const newPaneId = this.nextPaneId++;
        const paneEl = this.createPaneElement(newPaneId, savedPane.session || 'Terminal');

        const container = document.getElementById('term-panes-container');
        container.appendChild(paneEl);

        // Create terminal instance for restored pane
        this.initializeTerminalForPane(paneEl, {
          session_name: savedPane.session,
          directory: savedPane.directory || '~',
        });

        resolve();
      });
    }
  }

  // Initialize multi-session manager when DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    const root = document.querySelector('[data-term-root]');
    if (!root) return;

    const boxName = root.dataset.boxName || root.dataset.host || '';
    const session = root.dataset.session || 'default';
    const directory = root.dataset.directory || '/';

    // Wait for term.js to initialize the first terminal
    setTimeout(() => {
      if (window.termInstance) {
        window.multiSessionManager = new MultiSessionManager(boxName, session, directory);
      }
    }, 1000);
  });
})();
