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
  });
})();
