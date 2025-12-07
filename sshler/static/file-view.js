(function () {
  function getToken() {
    if (window.sshlerToken) {
      return window.sshlerToken;
    }
    const tokenMeta = document.querySelector('meta[name="sshler-token"]');
    return tokenMeta ? tokenMeta.getAttribute("content") || "" : "";
  }

  document.addEventListener("DOMContentLoaded", () => {
    const backBtn = document.getElementById("back-btn");
    const deleteBtn = document.getElementById("delete-btn");
    const toggleViewBtn = document.getElementById("toggle-view-btn");
    const toggleLineNumbers = document.getElementById("toggle-line-numbers");
    const toggleWordWrap = document.getElementById("toggle-word-wrap");
    const markdownRendered = document.getElementById("markdown-rendered");
    const markdownSource = document.getElementById("markdown-source");
    const fileContent = document.querySelector(".file-view__content");

    // Toggle between rendered and source view for markdown
    if (toggleViewBtn && markdownRendered && markdownSource) {
      let showingRendered = true;
      toggleViewBtn.addEventListener("click", () => {
        if (showingRendered) {
          markdownRendered.classList.add("hidden");
          markdownSource.classList.remove("hidden");
          toggleViewBtn.textContent = "View Rendered";
          showingRendered = false;
        } else {
          markdownRendered.classList.remove("hidden");
          markdownSource.classList.add("hidden");
          toggleViewBtn.textContent = "View Source";
          showingRendered = true;
        }
      });
    }

    // Apply Prism syntax highlighting to markdown code blocks
    if (markdownRendered && window.Prism) {
      markdownRendered.querySelectorAll("pre code").forEach((block) => {
        window.Prism.highlightElement(block);
      });
    }

    if (backBtn) {
      backBtn.addEventListener("click", (event) => {
        event.preventDefault();
        window.close();
      });
    }

    if (deleteBtn) {
      deleteBtn.addEventListener("click", async (event) => {
        event.preventDefault();
        const fileName = deleteBtn.dataset.filename || "this file";

        if (!confirm(`Delete ${fileName}?`)) {
          return;
        }

        const boxName = deleteBtn.dataset.box;
        const filePath = deleteBtn.dataset.path;
        const parentDir = deleteBtn.dataset.parentdir;

        try {
          const response = await fetch(`/box/${boxName}/delete`, {
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded",
              "X-SSHLER-TOKEN": getToken(),
            },
            body: new URLSearchParams({
              path: filePath,
              directory: parentDir,
              target: "browser",
            }),
          });

          if (response.ok) {
            window.close();
          } else {
            alert("Failed to delete file");
          }
        } catch (err) {
          console.error("Delete failed:", err);
          alert("Failed to delete file");
        }
      });
    }

    // Toggle line numbers
    if (toggleLineNumbers && fileContent) {
      const codeElement = fileContent.querySelector('code');

      // Check saved preference
      const lineNumbersPref = localStorage.getItem('file-view-line-numbers');
      if (lineNumbersPref === 'true') {
        enableLineNumbers(fileContent, codeElement);
        toggleLineNumbers.classList.add('active');
      }

      toggleLineNumbers.addEventListener('click', () => {
        const enabled = fileContent.classList.contains('line-numbers');
        if (enabled) {
          disableLineNumbers(fileContent, codeElement);
          toggleLineNumbers.classList.remove('active');
          localStorage.setItem('file-view-line-numbers', 'false');
        } else {
          enableLineNumbers(fileContent, codeElement);
          toggleLineNumbers.classList.add('active');
          localStorage.setItem('file-view-line-numbers', 'true');
        }
      });
    }

    function enableLineNumbers(preElement, codeElement) {
      if (!codeElement || preElement.classList.contains('line-numbers')) return;

      const text = codeElement.textContent;
      const lines = text.split('\n');

      // Wrap each line in a span
      codeElement.innerHTML = lines.map(line =>
        `<span class="line">${escapeHtml(line)}\n</span>`
      ).join('');

      preElement.classList.add('line-numbers');
    }

    function disableLineNumbers(preElement, codeElement) {
      if (!codeElement || !preElement.classList.contains('line-numbers')) return;

      // Extract text from spans
      const lines = Array.from(codeElement.querySelectorAll('.line'));
      const text = lines.map(span => span.textContent).join('');

      codeElement.textContent = text;
      preElement.classList.remove('line-numbers');
    }

    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    // Toggle word wrap
    if (toggleWordWrap && fileContent) {
      // Check saved preference
      const wordWrapPref = localStorage.getItem('file-view-word-wrap');
      if (wordWrapPref === 'true') {
        fileContent.classList.add('word-wrap');
        toggleWordWrap.classList.add('active');
      }

      toggleWordWrap.addEventListener('click', () => {
        fileContent.classList.toggle('word-wrap');
        toggleWordWrap.classList.toggle('active');
        const enabled = fileContent.classList.contains('word-wrap');
        localStorage.setItem('file-view-word-wrap', enabled);
      });
    }
  });
})();
