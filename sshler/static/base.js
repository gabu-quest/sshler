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
})();
