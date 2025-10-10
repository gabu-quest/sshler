(function () {
  const LANG_KEY = "sshler-language";

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

  function switchLanguage(lang) {
    const contents = document.querySelectorAll(".lang-content");
    const buttons = document.querySelectorAll(".lang-btn");

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

    setStoredLang(lang);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const modal = document.getElementById("docs-modal");
    const closeBtn = modal.querySelector(".modal-close");
    const langButtons = modal.querySelectorAll(".lang-btn");

    // Show modal immediately
    modal.classList.add("visible");

    // Set initial language
    const currentLang = getStoredLang();
    switchLanguage(currentLang);

    // Language switcher
    langButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        switchLanguage(btn.dataset.lang);
      });
    });

    // Close button
    closeBtn.addEventListener("click", () => {
      window.close();
    });

    // Close on outside click
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        window.close();
      }
    });

    // Close on Escape key
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        window.close();
      }
    });
  });

  // Export for use in other pages
  window.sshlerSwitchLanguage = switchLanguage;
  window.sshlerGetLanguage = getStoredLang;
})();
