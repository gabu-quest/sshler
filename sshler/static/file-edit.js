(function () {
  const MODE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "javascript",
    ".json": "application/json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".sh": "shell",
    ".bash": "shell",
    ".css": "css",
    ".html": "xml",
  };

  function resolveMode(path) {
    const idx = path.lastIndexOf(".");
    if (idx === -1) {
      return "plaintext";
    }
    const ext = path.slice(idx).toLowerCase();
    return MODE_MAP[ext] || "plaintext";
  }

  async function saveFile(cm, path, saveStatus) {
    const payload = {
      path,
      content: cm.getValue(),
    };
    saveStatus.textContent = "Saving...";
    try {
      const response = await fetch(
        window.location.pathname + window.location.search,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-SSHLER-TOKEN": window.sshlerToken || "",
          },
          body: JSON.stringify(payload),
        },
      );
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || response.statusText);
      }
      saveStatus.textContent = "Saved ✓";
      // Redirect to preview mode after successful save
      setTimeout(() => {
        const previewUrl = window.location.pathname.replace('/edit', '/cat') + window.location.search;
        window.location.href = previewUrl;
      }, 500);
    } catch (error) {
      console.error(error);
      saveStatus.textContent = `Save failed: ${error.message}`;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const textarea = document.getElementById("editor");
    if (!textarea) {
      return;
    }
    const saveStatus = document.getElementById("save-status");
    const path = textarea.dataset.path || "";
    const cm = CodeMirror.fromTextArea(textarea, {
      mode: resolveMode(path),
      theme: "default",
      lineNumbers: true,
      lineWrapping: true,
    });

    const saveBtn = document.getElementById("save-btn");
    if (saveBtn) {
      saveBtn.addEventListener("click", (event) => {
        event.preventDefault();
        saveFile(cm, path, saveStatus);
      });
    }
  });
})();
