(function () {
  const FAVICONS = {
    default: "/static/favicon.svg",
    terminal: "/static/favicon-terminal.svg",
    "terminal-local": "/static/favicon-terminal-local.svg",
  };
  const LANG_KEY = "sshler-language";

  const I18N = {
    en: {
      "nav.boxes": "Boxes",
      "nav.addBox": "Add Box",
      "nav.docs": "Docs",
      "boxes.title": "Boxes",
      "boxes.subtitle": "Pick a box to browse and open a terminal. Hosts are imported from your SSH config and any custom boxes you add here.",
      "boxes.localWorkspace": "Local workspace",
      "boxes.fromSSHConfig": "From SSH config",
      "boxes.customBox": "Custom box",
      "boxes.resolvesTo": "resolves to",
      "boxes.favorites": "Favorites",
      "boxes.open": "Open",
      "boxes.terminal": "Terminal",
      "boxes.refresh": "Refresh",
      "boxes.configFile": "Config file:",
      "box.browse": "Browse",
      "box.name": "Name",
      "box.type": "Type",
      "box.size": "Size",
      "box.actions": "Actions",
      "box.preview": "Preview",
      "box.edit": "Edit",
      "box.delete": "Delete",
      "box.createFile": "Create File",
      "box.uploadFile": "Upload File",
      "box.filename": "Filename",
      "box.create": "Create",
      "box.upload": "Upload",
      "term.session": "Session:",
      "term.back": "Back",
      "term.files": "Files",
      "term.scrollMode": "Scroll Mode",
      "term.escape": "Escape",
      "term.ctrlT": "Ctrl+T",
      "term.ctrlC": "Ctrl+C",
      "term.splitH": "Split %",
      "term.splitV": "Split \"",
      "term.newWindow": "New Window",
      "term.renameWindow": "Rename Window",
      "term.killPane": "Kill Pane",
      "term.nextWindow": "Next Window",
      "term.prevWindow": "Prev Window",
      "term.detach": "Detach",
    },
    ja: {
      "nav.boxes": "ボックス",
      "nav.addBox": "ボックスを追加",
      "nav.docs": "ドキュメント",
      "boxes.title": "ボックス",
      "boxes.subtitle": "ボックスを選択してファイルブラウザとターミナルを開きます。SSH 設定からホストが自動的にインポートされ、カスタムボックスも追加できます。",
      "boxes.localWorkspace": "ローカルワークスペース",
      "boxes.fromSSHConfig": "SSH 設定から",
      "boxes.customBox": "カスタムボックス",
      "boxes.resolvesTo": "解決先",
      "boxes.favorites": "お気に入り",
      "boxes.open": "開く",
      "boxes.terminal": "ターミナル",
      "boxes.refresh": "更新",
      "boxes.configFile": "設定ファイル:",
      "box.browse": "ブラウズ",
      "box.name": "名前",
      "box.type": "種類",
      "box.size": "サイズ",
      "box.actions": "操作",
      "box.preview": "プレビュー",
      "box.edit": "編集",
      "box.delete": "削除",
      "box.createFile": "ファイル作成",
      "box.uploadFile": "ファイルアップロード",
      "box.filename": "ファイル名",
      "box.create": "作成",
      "box.upload": "アップロード",
      "term.session": "セッション:",
      "term.back": "戻る",
      "term.files": "ファイル",
      "term.scrollMode": "スクロールモード",
      "term.escape": "Escape",
      "term.ctrlT": "Ctrl+T",
      "term.ctrlC": "Ctrl+C",
      "term.splitH": "横分割 %",
      "term.splitV": "縦分割 \"",
      "term.newWindow": "新規ウィンドウ",
      "term.renameWindow": "ウィンドウ名変更",
      "term.killPane": "ペイン終了",
      "term.nextWindow": "次のウィンドウ",
      "term.prevWindow": "前のウィンドウ",
      "term.detach": "デタッチ",
    },
  };

  function readToken() {
    const tokenMeta = document.querySelector('meta[name="sshler-token"]');
    const token = tokenMeta ? tokenMeta.getAttribute("content") : null;
    return token || "";
  }

  function applyToken(token) {
    if (!token) {
      return;
    }
    window.sshlerToken = token;

    // Configure htmx headers immediately if available
    if (window.htmx) {
      window.htmx.config.headers = window.htmx.config.headers || {};
      window.htmx.config.headers["X-SSHLER-TOKEN"] = token;
    }

    // Also set up event listener to add header to all htmx requests
    document.body.addEventListener("htmx:configRequest", (event) => {
      event.detail.headers["X-SSHLER-TOKEN"] = token;
    });
  }

  function setFavicon(mode) {
    const faviconLink = document.getElementById("favicon-link");
    if (!faviconLink) {
      return;
    }
    const target = FAVICONS[mode] || FAVICONS.default;
    if (faviconLink.getAttribute("href") !== target) {
      faviconLink.setAttribute("href", target);
    }
  }

  function showToast(message, type) {
    if (!message) {
      return;
    }
    const container = document.getElementById("toast-container");
    if (!container) {
      return;
    }
    const toast = document.createElement("div");
    toast.className = `toast ${type || "info"}`;
    toast.textContent = message;

    // Add ARIA role for screen readers
    if (type === "error") {
      toast.setAttribute("role", "alert");
    } else {
      toast.setAttribute("role", "status");
    }

    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add("visible"));
    setTimeout(() => {
      toast.classList.remove("visible");
      toast.addEventListener(
        "transitionend",
        () => toast.remove(),
        { once: true },
      );
    }, 3600);
  }

  // Export showToast globally for use in other scripts
  window.showToast = showToast;

  function getStoredLang() {
    try {
      return localStorage.getItem(LANG_KEY) || "en";
    } catch (err) {
      return "en";
    }
  }

  function setStoredLang(lang) {
    try {
      localStorage.setItem(LANG_KEY, lang);
    } catch (err) {
      // Ignore if localStorage is unavailable
    }
  }

  function translate(lang) {
    const elements = document.querySelectorAll("[data-i18n]");
    elements.forEach((el) => {
      const key = el.dataset.i18n;
      const text = I18N[lang]?.[key];
      if (text) {
        el.textContent = text;
      }
    });

    // Also translate placeholders
    const placeholderElements = document.querySelectorAll("[data-i18n-placeholder]");
    placeholderElements.forEach((el) => {
      const key = el.dataset.i18nPlaceholder;
      const text = I18N[lang]?.[key];
      if (text && el.tagName === "INPUT") {
        el.placeholder = text;
      }
    });
  }

  function updateLangToggle(lang) {
    const langToggle = document.getElementById("lang-toggle");
    if (!langToggle) {
      return;
    }
    const spans = langToggle.querySelectorAll("span");
    spans.forEach((span) => {
      if (span.dataset.lang === lang) {
        span.classList.remove("hidden");
      } else {
        span.classList.add("hidden");
      }
    });
  }

  function switchLanguage(newLang) {
    setStoredLang(newLang);
    updateLangToggle(newLang);
    translate(newLang);

    // Update docs modal if it's open
    const docsModal = document.querySelector("#modal-container .modal");
    if (docsModal) {
      switchDocsLanguage(newLang);
    }
  }

  function switchDocsLanguage(lang) {
    const modal = document.querySelector("#modal-container #docs-modal");
    if (!modal) return;

    const contents = modal.querySelectorAll(".lang-content");
    const buttons = modal.querySelectorAll(".lang-btn");

    contents.forEach((el) => {
      if (el.dataset.lang === lang) {
        el.classList.remove("hidden");
      } else {
        el.classList.add("hidden");
      }
    });

    buttons.forEach((btn) => {
      if (btn.dataset.lang === lang) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    });
  }

  // Theme management
  const THEME_KEY = "sshler-theme";

  function getStoredTheme() {
    try {
      return localStorage.getItem(THEME_KEY) || null;
    } catch (err) {
      return null;
    }
  }

  function setStoredTheme(theme) {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (err) {
      // Ignore if localStorage is unavailable
    }
  }

  function getSystemTheme() {
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
      return 'light';
    }
    return 'dark';
  }

  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === 'light' || theme === 'dark') {
      root.setAttribute('data-theme', theme);
    } else {
      // Use system preference
      root.removeAttribute('data-theme');
    }
  }

  function updateThemeToggle(theme) {
    const themeToggle = document.getElementById("theme-toggle");
    if (!themeToggle) {
      return;
    }
    const currentTheme = theme || getSystemTheme();
    const spans = themeToggle.querySelectorAll("span");
    spans.forEach((span) => {
      if (span.dataset.themeIcon === currentTheme) {
        span.classList.remove("hidden");
      } else {
        span.classList.add("hidden");
      }
    });
  }

  function switchTheme() {
    const stored = getStoredTheme();
    const currentTheme = stored || getSystemTheme();
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

    setStoredTheme(newTheme);
    applyTheme(newTheme);
    updateThemeToggle(newTheme);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const token = readToken();
    applyToken(token);
    setFavicon("default");

    // Initialize language toggle
    const currentLang = getStoredLang();
    updateLangToggle(currentLang);
    translate(currentLang);

    const langToggle = document.getElementById("lang-toggle");
    if (langToggle) {
      langToggle.addEventListener("click", () => {
        const current = getStoredLang();
        const newLang = current === "en" ? "ja" : "en";
        switchLanguage(newLang);
      });
    }

    // Initialize theme
    const storedTheme = getStoredTheme();
    if (storedTheme) {
      applyTheme(storedTheme);
      updateThemeToggle(storedTheme);
    } else {
      // Use system preference
      const systemTheme = getSystemTheme();
      updateThemeToggle(systemTheme);
    }

    // Theme toggle button handler
    const themeToggle = document.getElementById("theme-toggle");
    if (themeToggle) {
      themeToggle.addEventListener("click", () => {
        switchTheme();
      });
    }

    // Listen for system theme changes
    if (window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => {
        const storedTheme = getStoredTheme();
        if (!storedTheme) {
          // Only update if user hasn't set a preference
          const newTheme = e.matches ? 'light' : 'dark';
          updateThemeToggle(newTheme);
        }
      });
    }

    // Docs button handler
    const docsBtn = document.getElementById("docs-btn");
    if (docsBtn) {
      docsBtn.addEventListener("click", async () => {
        const modalContainer = document.getElementById("modal-container");
        if (!modalContainer) return;

        try {
          const response = await fetch("/docs");
          if (!response.ok) throw new Error("Failed to load docs");
          const html = await response.text();
          modalContainer.innerHTML = html;

          const modal = modalContainer.querySelector("#docs-modal");
          if (!modal) return;

          // Show modal
          modal.classList.add("visible");

          // Set initial language to match current site language
          const currentLang = getStoredLang();
          switchDocsLanguage(currentLang);

          // Language switcher in modal
          const langButtons = modal.querySelectorAll(".lang-btn");
          langButtons.forEach((btn) => {
            btn.addEventListener("click", () => {
              const newLang = btn.dataset.lang;
              switchDocsLanguage(newLang);
              setStoredLang(newLang);
              updateLangToggle(newLang);
              translate(newLang);
            });
          });

          // Close button
          const closeBtn = modal.querySelector(".modal-close");
          if (closeBtn) {
            closeBtn.addEventListener("click", () => {
              modal.classList.remove("visible");
              setTimeout(() => {
                modalContainer.innerHTML = "";
              }, 300);
            });
          }

          // Close on outside click
          modal.addEventListener("click", (event) => {
            if (event.target === modal) {
              modal.classList.remove("visible");
              setTimeout(() => {
                modalContainer.innerHTML = "";
              }, 300);
            }
          });

          // Close on Escape key
          const escHandler = (event) => {
            if (event.key === "Escape") {
              modal.classList.remove("visible");
              setTimeout(() => {
                modalContainer.innerHTML = "";
              }, 300);
              document.removeEventListener("keydown", escHandler);
            }
          };
          document.addEventListener("keydown", escHandler);
        } catch (err) {
          console.error("Failed to load docs:", err);
          showToast("Failed to load documentation", "error");
        }
      });
    }

    document.body.addEventListener("dir-action", (event) => {
      const payload = event.detail && event.detail.value;
      if (!payload) {
        return;
      }
      const status = payload.status === "error" ? "error" : "success";
      showToast(payload.message, status);
    });

    // Re-translate after HTMX swaps new content
    document.body.addEventListener("htmx:afterSwap", () => {
      const currentLang = getStoredLang();
      translate(currentLang);
    });

    // Event delegation for delete buttons
    document.body.addEventListener("click", (event) => {
      const deleteBtn = event.target.closest(".delete-file-btn");
      if (!deleteBtn) {
        return;
      }
      event.preventDefault();
      const boxName = deleteBtn.dataset.box;
      const filePath = deleteBtn.dataset.path;
      const directory = deleteBtn.dataset.directory;
      const target = deleteBtn.dataset.target;
      const fileName = deleteBtn.dataset.filename;
      deleteFile(boxName, filePath, directory, target, fileName);
    });

    // Register service worker for PWA support
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/static/sw.js').then((registration) => {
        console.log('[PWA] Service Worker registered:', registration.scope);

        // Check for updates periodically
        // Use longer interval to avoid issues on mobile/flaky connections
        setInterval(() => {
          registration.update();
        }, 5 * 60000); // Check every 5 minutes

        // Handle updates
        registration.addEventListener('updatefound', () => {
          const newWorker = registration.installing;
          newWorker.addEventListener('statechange', () => {
            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
              // New version available
              if (confirm('A new version of sshler is available. Reload to update?')) {
                newWorker.postMessage({ type: 'SKIP_WAITING' });
                window.location.reload();
              }
            }
          });
        });
      }).catch((error) => {
        console.error('[PWA] Service Worker registration failed:', error);
      });

      // Reload page when new service worker takes control
      // Add delay to prevent rapid refreshes on flaky mobile connections
      let refreshing = false;
      let lastRefreshTime = 0;
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        const now = Date.now();
        const timeSinceLastRefresh = now - lastRefreshTime;

        // Don't refresh if we just refreshed within the last 30 seconds
        if (!refreshing && timeSinceLastRefresh > 30000) {
          refreshing = true;
          lastRefreshTime = now;
          window.location.reload();
        }
      });
    }
  });

  function deleteFile(boxName, filePath, directory, target, fileName) {
    if (!confirm(`Delete ${fileName}?`)) {
      return;
    }

    const token = window.sshlerToken || readToken();
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/box/${boxName}/delete`);
    xhr.setRequestHeader("X-SSHLER-TOKEN", token);
    xhr.onload = function () {
      const browserEl = document.getElementById(target);
      if (browserEl && xhr.status === 200) {
        browserEl.innerHTML = xhr.responseText;
      } else {
        showToast("Failed to delete file", "error");
      }
    };
    xhr.onerror = function () {
      showToast("Failed to delete file", "error");
    };

    const formData = new FormData();
    formData.append("path", filePath);
    formData.append("directory", directory);
    formData.append("target", target);
    xhr.send(formData);
  }

  window.sshlerShowToast = showToast;
  window.sshlerSetFavicon = setFavicon;
  window.sshlerDeleteFile = deleteFile;
  window.sshlerTranslate = function() {
    translate(getStoredLang());
  };

  // Keyboard shortcuts
  let shortcutsModal = null;

  function showKeyboardShortcuts() {
    if (shortcutsModal) {
      shortcutsModal.classList.add('visible');
      // Update categories based on current page
      updateShortcutCategories();
      return;
    }

    const modalContainer = document.getElementById('modal-container');
    if (!modalContainer) return;

    const shortcuts = {
      'Global': [
        { key: '?', desc: 'Show keyboard shortcuts' },
        { key: 'Cmd/Ctrl+K', desc: 'Open command palette' },
        { key: 'Cmd/Ctrl+/', desc: 'Open global search' },
        { key: 'Esc', desc: 'Close modals / Clear search' },
      ],
      'Navigation': [
        { key: '/', desc: 'Focus search (on boxes page)' },
        { key: 'n', desc: 'New box (on boxes page)' },
        { key: 'Click Recent Files', desc: 'View recent files and bookmarks' },
      ],
      'File Browser': [
        { key: 'Ctrl/Cmd+F', desc: 'Search files in directory' },
        { key: 'Right-click', desc: 'Context menu on files' },
        { key: 'Double-click name', desc: 'Rename file' },
        { key: 'Drag & Drop', desc: 'Move files to folders' },
        { key: 'Checkbox + Delete', desc: 'Bulk delete files' },
      ],
      'Terminal': [
        { key: 'Ctrl+F', desc: 'Search in terminal' },
        { key: 'Ctrl+L', desc: 'Clear terminal' },
        { key: '+/-', desc: 'Adjust font size' },
        { key: 'Scroll Mode', desc: 'Enable mouse scrolling' },
      ],
      'Theme & Display': [
        { key: 'Click Theme Toggle', desc: 'Switch light/dark theme' },
        { key: 'Click Language', desc: 'Switch EN/JP' },
      ],
    };

    const html = `
      <div class="modal visible" id="shortcuts-modal">
        <div class="modal-content shortcuts-modal-content">
          <div class="modal-header">
            <h2>⌨️ Keyboard Shortcuts</h2>
            <button class="modal-close" id="shortcuts-close">×</button>
          </div>
          <div class="shortcuts-search-container">
            <input type="text"
                   id="shortcuts-search"
                   class="shortcuts-search-input"
                   placeholder="Search shortcuts..."
                   aria-label="Search shortcuts">
          </div>
          <div class="modal-body shortcuts-modal-body">
            ${Object.entries(shortcuts).map(([category, items]) => `
              <div class="shortcuts-category" data-category="${category}">
                <h3 class="shortcuts-category-title">${category}</h3>
                <div class="shortcuts-list">
                  ${items.map(s => `
                    <div class="shortcuts-item" data-search="${s.key.toLowerCase()} ${s.desc.toLowerCase()}">
                      <kbd class="shortcuts-key">${s.key}</kbd>
                      <span class="shortcuts-desc">${s.desc}</span>
                    </div>
                  `).join('')}
                </div>
              </div>
            `).join('')}
          </div>
          <div class="shortcuts-footer">
            <p>Tip: Most shortcuts work context-aware based on the current page</p>
          </div>
        </div>
      </div>
    `;

    modalContainer.innerHTML = html;
    shortcutsModal = modalContainer.querySelector('#shortcuts-modal');

    const closeBtn = shortcutsModal.querySelector('#shortcuts-close');
    const searchInput = shortcutsModal.querySelector('#shortcuts-search');

    closeBtn?.addEventListener('click', () => {
      shortcutsModal.classList.remove('visible');
    });

    shortcutsModal.addEventListener('click', (e) => {
      if (e.target === shortcutsModal) {
        shortcutsModal.classList.remove('visible');
      }
    });

    // Search functionality
    searchInput?.addEventListener('input', (e) => {
      const query = e.target.value.toLowerCase();
      const items = shortcutsModal.querySelectorAll('.shortcuts-item');
      const categories = shortcutsModal.querySelectorAll('.shortcuts-category');

      if (!query) {
        items.forEach(item => item.style.display = 'flex');
        categories.forEach(cat => cat.style.display = 'block');
        return;
      }

      items.forEach(item => {
        const searchText = item.dataset.search || '';
        if (searchText.includes(query)) {
          item.style.display = 'flex';
        } else {
          item.style.display = 'none';
        }
      });

      // Hide empty categories
      categories.forEach(cat => {
        const visibleItems = cat.querySelectorAll('.shortcuts-item[style="display: flex;"]');
        cat.style.display = visibleItems.length > 0 ? 'block' : 'none';
      });
    });

    updateShortcutCategories();
  }

  function updateShortcutCategories() {
    if (!shortcutsModal) return;

    const currentPath = window.location.pathname;
    const categories = shortcutsModal.querySelectorAll('.shortcuts-category');

    categories.forEach(cat => {
      const category = cat.dataset.category;

      // Show/hide based on context
      if (category === 'File Browser' && !currentPath.includes('/box/')) {
        cat.classList.add('context-hidden');
      } else if (category === 'Terminal' && !currentPath.includes('/term')) {
        cat.classList.add('context-hidden');
      } else {
        cat.classList.remove('context-hidden');
      }
    });
  }

  // Global keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    // Don't trigger if typing in an input
    if (e.target.matches('input, textarea')) {
      // Esc clears search
      if (e.key === 'Escape' && e.target.matches('input[type="text"]')) {
        e.target.value = '';
        e.target.dispatchEvent(new Event('input'));
        e.target.blur();
      }
      return;
    }

    // Show shortcuts
    if (e.key === '?' || (e.shiftKey && e.key === '/')) {
      e.preventDefault();
      showKeyboardShortcuts();
      return;
    }

    // Close modal with Esc
    if (e.key === 'Escape') {
      const modal = document.querySelector('.modal.visible');
      if (modal) {
        modal.classList.remove('visible');
        return;
      }
    }

    // Focus search on /
    if (e.key === '/' && window.location.pathname === '/boxes') {
      e.preventDefault();
      const searchInput = document.getElementById('box-search');
      searchInput?.focus();
      return;
    }

    // New box on n
    if (e.key === 'n' && window.location.pathname === '/boxes') {
      e.preventDefault();
      window.location.href = '/boxes/new';
      return;
    }
  });

  // === RECENT FILES & BOOKMARKS ===
  const RECENT_FILES_KEY = 'sshler-recent-files';
  const BOOKMARKS_KEY = 'sshler-bookmarks';
  const MAX_RECENT_FILES = 10;

  function getRecentFiles() {
    try {
      return JSON.parse(localStorage.getItem(RECENT_FILES_KEY) || '[]');
    } catch {
      return [];
    }
  }

  function getBookmarks() {
    try {
      return JSON.parse(localStorage.getItem(BOOKMARKS_KEY) || '[]');
    } catch {
      return [];
    }
  }

  function addRecentFile(fileInfo) {
    const recent = getRecentFiles();

    // Remove if already exists
    const filtered = recent.filter(f => f.path !== fileInfo.path);

    // Add to front
    filtered.unshift({
      ...fileInfo,
      timestamp: Date.now(),
    });

    // Keep only MAX_RECENT_FILES
    const trimmed = filtered.slice(0, MAX_RECENT_FILES);

    localStorage.setItem(RECENT_FILES_KEY, JSON.stringify(trimmed));
    updateRecentFilesUI();
  }

  function toggleBookmark(fileInfo) {
    const bookmarks = getBookmarks();
    const exists = bookmarks.findIndex(b => b.path === fileInfo.path);

    if (exists >= 0) {
      // Remove bookmark
      bookmarks.splice(exists, 1);
    } else {
      // Add bookmark
      bookmarks.push({
        ...fileInfo,
        timestamp: Date.now(),
      });
    }

    localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(bookmarks));
    updateRecentFilesUI();
  }

  function clearRecentFiles() {
    localStorage.removeItem(RECENT_FILES_KEY);
    updateRecentFilesUI();
  }

  function updateRecentFilesUI() {
    const dropdownContent = document.querySelector('.recent-files-dropdown-content');
    if (!dropdownContent) return;

    const recent = getRecentFiles();
    const bookmarks = getBookmarks();

    let html = '';

    // Bookmarks section
    if (bookmarks.length > 0) {
      html += '<div class="recent-section-header">Bookmarks</div>';
      bookmarks.forEach(file => {
        html += `
          <a href="${file.url}" class="recent-file-item" data-path="${file.path}">
            <span class="recent-file-icon">⭐</span>
            <span class="recent-file-name">${file.name}</span>
            <span class="recent-file-box">${file.boxName}</span>
          </a>
        `;
      });
    }

    // Recent files section
    if (recent.length > 0) {
      if (bookmarks.length > 0) {
        html += '<div class="recent-section-divider"></div>';
      }
      html += '<div class="recent-section-header">Recent Files</div>';
      recent.forEach(file => {
        const timeAgo = formatTimeAgo(file.timestamp);
        html += `
          <a href="${file.url}" class="recent-file-item" data-path="${file.path}">
            <span class="recent-file-icon">📄</span>
            <span class="recent-file-name">${file.name}</span>
            <span class="recent-file-time">${timeAgo}</span>
          </a>
        `;
      });

      html += '<div class="recent-section-divider"></div>';
      html += `
        <button class="recent-clear-btn" id="clear-recent-files">
          Clear Recent Files
        </button>
      `;
    }

    if (html === '') {
      html = '<div class="recent-empty">No recent files or bookmarks</div>';
    }

    dropdownContent.innerHTML = html;

    // Add clear button handler
    const clearBtn = document.getElementById('clear-recent-files');
    if (clearBtn) {
      clearBtn.addEventListener('click', (e) => {
        e.preventDefault();
        clearRecentFiles();
      });
    }
  }

  function formatTimeAgo(timestamp) {
    const seconds = Math.floor((Date.now() - timestamp) / 1000);

    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
    return `${Math.floor(seconds / 604800)}w ago`;
  }

  function initRecentFiles() {
    const header = document.querySelector('.topbar');
    if (!header) return;

    // Create recent files dropdown button
    const recentBtn = document.createElement('button');
    recentBtn.id = 'recent-files-btn';
    recentBtn.className = 'link recent-files-btn';
    recentBtn.setAttribute('aria-label', 'Recent files and bookmarks');
    recentBtn.innerHTML = '📋';
    recentBtn.title = 'Recent Files & Bookmarks';

    // Create dropdown
    const dropdown = document.createElement('div');
    dropdown.className = 'recent-files-dropdown';
    dropdown.innerHTML = `
      <div class="recent-files-dropdown-content"></div>
    `;

    // Insert button before theme toggle
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
      header.insertBefore(recentBtn, themeToggle);
      header.insertBefore(dropdown, themeToggle);
    }

    // Toggle dropdown
    recentBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      dropdown.classList.toggle('visible');
      updateRecentFilesUI();
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', () => {
      dropdown.classList.remove('visible');
    });

    dropdown.addEventListener('click', (e) => {
      e.stopPropagation();
    });

    updateRecentFilesUI();
  }

  // Track file views automatically
  function trackFileView() {
    // Check if we're viewing a file (preview or edit page)
    const pathname = window.location.pathname;
    const params = new URLSearchParams(window.location.search);
    const filePath = params.get('path');

    if (!filePath) return;

    // Extract box name from pathname
    let boxName = '';
    let url = '';

    if (pathname.includes('/box/') && pathname.includes('/cat')) {
      // Preview page: /box/{name}/cat?path=...
      const match = pathname.match(/\/box\/([^\/]+)\/cat/);
      if (match) {
        boxName = match[1];
        url = pathname + '?' + params.toString();
      }
    } else if (pathname.includes('/box/') && pathname.includes('/edit')) {
      // Edit page: /box/{name}/edit?path=...
      const match = pathname.match(/\/box\/([^\/]+)\/edit/);
      if (match) {
        boxName = match[1];
        url = pathname + '?' + params.toString();
      }
    }

    if (boxName && url) {
      const fileName = filePath.split('/').pop();
      addRecentFile({
        name: fileName,
        path: filePath,
        boxName: boxName,
        url: url,
      });
    }
  }

  // Export functions for use in file browser
  window.sshlerAddRecentFile = addRecentFile;
  window.sshlerToggleBookmark = toggleBookmark;

  // === GLOBAL SEARCH ===
  function initGlobalSearch() {
    const header = document.querySelector('.topbar');
    if (!header) return;

    // Create search input
    const searchContainer = document.createElement('div');
    searchContainer.className = 'global-search-container';
    searchContainer.innerHTML = `
      <input type="text"
             id="global-search-input"
             class="global-search-input"
             placeholder="Search..."
             aria-label="Global search">
      <span class="global-search-icon">🔍</span>
    `;

    // Insert after spacer
    const spacer = header.querySelector('.spacer');
    if (spacer) {
      spacer.parentNode.insertBefore(searchContainer, spacer.nextSibling);
    }

    // Create search results modal
    const searchModal = document.createElement('div');
    searchModal.id = 'global-search-modal';
    searchModal.className = 'global-search-modal';
    searchModal.innerHTML = `
      <div class="global-search-backdrop"></div>
      <div class="global-search-results">
        <div class="global-search-header">
          <input type="text"
                 id="global-search-modal-input"
                 class="global-search-modal-input"
                 placeholder="Search across all boxes and files..."
                 aria-label="Global search">
          <button class="global-search-close" aria-label="Close search">✕</button>
        </div>
        <div class="global-search-results-container">
          <div class="global-search-empty">Start typing to search...</div>
        </div>
        <div class="global-search-footer">
          <span>↑↓ Navigate</span>
          <span>Enter Select</span>
          <span>Esc Close</span>
        </div>
      </div>
    `;
    document.body.appendChild(searchModal);

    const headerInput = document.getElementById('global-search-input');
    const modalInput = document.getElementById('global-search-modal-input');
    const resultsContainer = searchModal.querySelector('.global-search-results-container');
    const closeBtn = searchModal.querySelector('.global-search-close');
    const backdrop = searchModal.querySelector('.global-search-backdrop');

    let searchTimeout = null;
    let currentResults = [];
    let selectedIndex = -1;

    function showSearchModal() {
      searchModal.classList.add('visible');
      modalInput.value = headerInput.value;
      modalInput.focus();
      if (headerInput.value) {
        performSearch(headerInput.value);
      }
    }

    function hideSearchModal() {
      searchModal.classList.remove('visible');
      headerInput.value = '';
      modalInput.value = '';
      resultsContainer.innerHTML = '<div class="global-search-empty">Start typing to search...</div>';
      currentResults = [];
      selectedIndex = -1;
    }

    function performClientSideSearch(query) {
      // Search through visible box names and links
      const results = {
        boxes: [],
        files: []
      };

      const queryLower = query.toLowerCase();

      // Search box cards on boxes page
      const boxes = document.querySelectorAll('.box-card');
      boxes.forEach(box => {
        const nameEl = box.querySelector('.box-name');
        const hostEl = box.querySelector('.box-host');

        if (nameEl) {
          const boxName = nameEl.textContent;
          const boxHost = hostEl ? hostEl.textContent : '';

          if (boxName.toLowerCase().includes(queryLower) ||
              boxHost.toLowerCase().includes(queryLower)) {
            results.boxes.push({
              name: boxName,
              host: boxHost,
              url: `/box/${encodeURIComponent(boxName)}`
            });
          }
        }
      });

      displaySearchResults(results, query);
    }

    function displaySearchResults(results, query) {
      currentResults = [];
      let html = '';

      // Box results
      if (results.boxes && results.boxes.length > 0) {
        html += '<div class="search-section-header">Boxes</div>';
        results.boxes.forEach((box, idx) => {
          currentResults.push({ type: 'box', ...box });
          html += `
            <a href="${box.url || '/boxes'}" class="search-result-item" data-index="${idx}">
              <span class="search-result-icon">📦</span>
              <div class="search-result-content">
                <div class="search-result-title">${highlightMatch(box.name, query)}</div>
                ${box.host ? `<div class="search-result-meta">${box.host}</div>` : ''}
              </div>
            </a>
          `;
        });
      }

      // File results
      if (results.files && results.files.length > 0) {
        if (results.boxes && results.boxes.length > 0) {
          html += '<div class="search-section-divider"></div>';
        }
        html += '<div class="search-section-header">Files</div>';
        results.files.forEach((file, idx) => {
          const resultIdx = currentResults.length;
          currentResults.push({ type: 'file', ...file });
          html += `
            <a href="${file.url}" class="search-result-item" data-index="${resultIdx}">
              <span class="search-result-icon">📄</span>
              <div class="search-result-content">
                <div class="search-result-title">${highlightMatch(file.name, query)}</div>
                <div class="search-result-meta">${file.path} • ${file.boxName}</div>
              </div>
            </a>
          `;
        });
      }

      if (html === '') {
        html = `<div class="global-search-empty">No results found for "${query}"</div>`;
      }

      resultsContainer.innerHTML = html;
      selectedIndex = -1;
    }

    function highlightMatch(text, query) {
      const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
      return text.replace(regex, '<mark>$1</mark>');
    }

    function updateSelection() {
      const items = resultsContainer.querySelectorAll('.search-result-item');
      items.forEach((item, idx) => {
        if (idx === selectedIndex) {
          item.classList.add('selected');
          item.scrollIntoView({ block: 'nearest' });
        } else {
          item.classList.remove('selected');
        }
      });
    }

    // Header input - focus opens modal
    headerInput.addEventListener('focus', () => {
      showSearchModal();
    });

    // Modal input - search on type
    modalInput.addEventListener('input', (e) => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        const query = e.target.value;
        if (query.length >= 2) {
          performClientSideSearch(query);
        } else if (query.length === 0) {
          resultsContainer.innerHTML = '<div class="global-search-empty">Start typing to search...</div>';
        } else {
          resultsContainer.innerHTML = '<div class="global-search-empty">Type at least 2 characters...</div>';
        }
      }, 300);
    });

    // Keyboard navigation in modal
    modalInput.addEventListener('keydown', (e) => {
      const items = resultsContainer.querySelectorAll('.search-result-item');

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
        updateSelection();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        selectedIndex = Math.max(selectedIndex - 1, -1);
        updateSelection();
      } else if (e.key === 'Enter' && selectedIndex >= 0 && items[selectedIndex]) {
        e.preventDefault();
        items[selectedIndex].click();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        hideSearchModal();
      }
    });

    // Close handlers
    closeBtn.addEventListener('click', hideSearchModal);
    backdrop.addEventListener('click', hideSearchModal);

    // Global keyboard shortcut: Ctrl+/ or Cmd+/
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === '/' && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        showSearchModal();
      }
    });
  }

  // === CONNECTION STATUS INDICATORS ===
  function initConnectionStatus() {
    const header = document.querySelector('.topbar');
    if (!header) return;

    // Create status indicator
    const statusIndicator = document.createElement('div');
    statusIndicator.id = 'connection-status';
    statusIndicator.className = 'connection-status';
    statusIndicator.innerHTML = `
      <div class="connection-indicator">
        <span class="connection-dot"></span>
        <span class="connection-text">Connected</span>
      </div>
    `;

    // Insert before theme toggle
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
      header.insertBefore(statusIndicator, themeToggle);
    }

    const indicator = statusIndicator.querySelector('.connection-indicator');
    const dot = statusIndicator.querySelector('.connection-dot');
    const text = statusIndicator.querySelector('.connection-text');

    let reconnectAttempts = 0;
    const MAX_RECONNECT_ATTEMPTS = 5;

    function updateStatus(status, message) {
      indicator.className = `connection-indicator connection-${status}`;
      text.textContent = message || status;

      // Auto-hide "Connected" status after 3 seconds
      if (status === 'connected') {
        setTimeout(() => {
          if (indicator.classList.contains('connection-connected')) {
            statusIndicator.classList.add('minimized');
          }
        }, 3000);
      } else {
        statusIndicator.classList.remove('minimized');
      }
    }

    // Monitor network connectivity
    window.addEventListener('online', () => {
      updateStatus('connected', 'Connected');
      reconnectAttempts = 0;
    });

    window.addEventListener('offline', () => {
      updateStatus('disconnected', 'Offline');
    });

    // Monitor fetch/XHR errors
    const originalFetch = window.fetch;
    window.fetch = async function(...args) {
      try {
        const response = await originalFetch.apply(this, args);

        if (response.ok) {
          if (!indicator.classList.contains('connection-connected')) {
            updateStatus('connected', 'Connected');
            reconnectAttempts = 0;
          }
        } else if (response.status >= 500) {
          updateStatus('error', 'Server Error');
        }

        return response;
      } catch (error) {
        if (error.name === 'TypeError' && error.message.includes('Failed to fetch')) {
          reconnectAttempts++;

          if (reconnectAttempts <= MAX_RECONNECT_ATTEMPTS) {
            updateStatus('reconnecting', `Reconnecting (${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`);
          } else {
            updateStatus('disconnected', 'Connection Lost');
          }
        }
        throw error;
      }
    };

    // Monitor WebSocket connections (for terminal sessions)
    const originalWebSocket = window.WebSocket;
    let activeWebSockets = 0;

    window.WebSocket = function(...args) {
      const ws = new originalWebSocket(...args);
      activeWebSockets++;
      updateSessionCount();

      ws.addEventListener('open', () => {
        updateStatus('connected', 'Connected');
        reconnectAttempts = 0;
        updateSessionCount();
      });

      ws.addEventListener('close', () => {
        activeWebSockets = Math.max(0, activeWebSockets - 1);
        updateSessionCount();

        if (activeWebSockets === 0) {
          // Last WebSocket closed, show reconnecting if unexpected
          setTimeout(() => {
            if (activeWebSockets === 0) {
              updateStatus('idle', 'Connected');
            }
          }, 1000);
        }
      });

      ws.addEventListener('error', () => {
        updateStatus('error', 'Connection Error');
      });

      return ws;
    };

    function updateSessionCount() {
      if (activeWebSockets > 0) {
        text.textContent = `${activeWebSockets} active session${activeWebSockets !== 1 ? 's' : ''}`;
        statusIndicator.classList.remove('minimized');
      }
    }

    // Click to toggle minimized state
    statusIndicator.addEventListener('click', () => {
      statusIndicator.classList.toggle('minimized');
    });

    // Initial status
    updateStatus('connected', 'Connected');
  }

  // Initialize on page load
  document.addEventListener('DOMContentLoaded', () => {
    initRecentFiles();
    trackFileView();
    initGlobalSearch();
    initConnectionStatus();
  });
})();
