(function () {
  const FAVICONS = {
    default: "/static/favicon.svg",
    terminal: "/static/favicon-terminal.svg",
  };
  const LANG_KEY = "sshler-language";

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
    const target = mode === "terminal" ? FAVICONS.terminal : FAVICONS.default;
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

  document.addEventListener("DOMContentLoaded", () => {
    const token = readToken();
    applyToken(token);
    setFavicon("default");

    // Initialize language toggle
    const currentLang = getStoredLang();
    updateLangToggle(currentLang);

    const langToggle = document.getElementById("lang-toggle");
    if (langToggle) {
      langToggle.addEventListener("click", () => {
        const current = getStoredLang();
        const newLang = current === "en" ? "ja" : "en";
        setStoredLang(newLang);
        updateLangToggle(newLang);
        // Reload to apply language change
        window.location.reload();
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
})();
