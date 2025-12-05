(function() {
  'use strict';

  // === DRAG & DROP FILE UPLOAD ===
  function initDragAndDrop() {
    const browserContainer = document.querySelector('.dir-table');
    if (!browserContainer) return;

    const dropZone = browserContainer.closest('#browser') || browserContainer.parentElement;
    if (!dropZone) return;

    dropZone.classList.add('drop-zone');

    let dragCounter = 0;

    dropZone.addEventListener('dragenter', (e) => {
      e.preventDefault();
      dragCounter++;
      if (dragCounter === 1) {
        dropZone.classList.add('drag-over');
      }
    });

    dropZone.addEventListener('dragleave', (e) => {
      e.preventDefault();
      dragCounter--;
      if (dragCounter === 0) {
        dropZone.classList.remove('drag-over');
      }
    });

    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
    });

    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dragCounter = 0;
      dropZone.classList.remove('drag-over');

      const files = e.dataTransfer.files;
      if (files.length === 0) return;

      // Find the upload form
      const uploadForm = document.querySelector('form[hx-post*="/upload"]');
      if (!uploadForm) {
        window.sshlerShowToast?.('Upload form not found', 'error');
        return;
      }

      // Get the file input
      const fileInput = uploadForm.querySelector('input[type="file"]');
      if (!fileInput) return;

      // Create a new FileList (we can only set it via the form submission)
      const dataTransfer = new DataTransfer();
      for (let i = 0; i < files.length; i++) {
        dataTransfer.items.add(files[i]);
      }
      fileInput.files = dataTransfer.files;

      // Show toast
      window.sshlerShowToast?.(`Uploading ${files.length} file(s)...`, 'success');

      // Trigger the form submission
      uploadForm.requestSubmit();
    });
  }

  // === CONTEXT MENU ===
  let contextMenu = null;

  function createContextMenu() {
    if (contextMenu) return contextMenu;

    contextMenu = document.createElement('div');
    contextMenu.className = 'context-menu';
    contextMenu.innerHTML = `
      <div class="context-menu-item" data-action="open">📂 Open</div>
      <div class="context-menu-item" data-action="preview">👁️ Preview</div>
      <div class="context-menu-item" data-action="edit">✏️ Edit</div>
      <div class="context-menu-item" data-action="copy-path">📋 Copy Path</div>
      <div class="context-menu-separator"></div>
      <div class="context-menu-item danger" data-action="delete">🗑️ Delete</div>
    `;
    document.body.appendChild(contextMenu);

    // Close on click outside
    document.addEventListener('click', () => {
      contextMenu?.classList.remove('visible');
    });

    return contextMenu;
  }

  function showContextMenu(e, fileRow) {
    e.preventDefault();
    e.stopPropagation();

    const menu = createContextMenu();
    const isDirectory = fileRow.querySelector('.fs-entry.folder') !== null;
    const fileName = fileRow.querySelector('.fs-label')?.textContent;
    const filePath = fileRow.querySelector('[data-path]')?.dataset.path || fileName;

    // Position menu
    menu.style.left = `${e.pageX}px`;
    menu.style.top = `${e.pageY}px`;
    menu.classList.add('visible');

    // Handle menu items
    const items = menu.querySelectorAll('.context-menu-item');
    items.forEach(item => {
      const newItem = item.cloneNode(true);
      item.parentNode.replaceChild(newItem, item);

      newItem.addEventListener('click', (e) => {
        e.stopPropagation();
        const action = newItem.dataset.action;

        switch (action) {
          case 'open':
            if (isDirectory) {
              fileRow.querySelector('.fs-entry.folder')?.click();
            } else {
              fileRow.querySelector('.action-btn[title*="Preview"]')?.click();
            }
            break;
          case 'preview':
            fileRow.querySelector('.action-btn[title*="Preview"]')?.click();
            break;
          case 'edit':
            fileRow.querySelector('.action-btn[title*="Edit"]')?.click();
            break;
          case 'copy-path':
            const pathText = fileRow.closest('table')?.parentElement?.querySelector('.breadcrumb-item.active')?.textContent || '';
            const fullPath = pathText + '/' + fileName;
            navigator.clipboard.writeText(fullPath).then(() => {
              window.sshlerShowToast?.('Path copied to clipboard', 'success');
            }).catch(() => {
              window.sshlerShowToast?.('Failed to copy path', 'error');
            });
            break;
          case 'delete':
            fileRow.querySelector('.delete-file-btn')?.click();
            break;
        }

        menu.classList.remove('visible');
      });
    });
  }

  function initContextMenus() {
    // Use event delegation
    document.addEventListener('contextmenu', (e) => {
      const fileRow = e.target.closest('.fs-row');
      if (fileRow && fileRow.querySelector('.fs-entry')) {
        showContextMenu(e, fileRow);
      }
    });
  }

  // === FILE SEARCH ===
  function initFileSearch() {
    const dirHeader = document.querySelector('.dir-header');
    if (!dirHeader) return;

    // Only add if not already present
    if (document.querySelector('.file-search-container')) return;

    const searchContainer = document.createElement('div');
    searchContainer.className = 'file-search-container';
    searchContainer.innerHTML = `
      <div class="file-search-box">
        <span class="file-search-icon">🔍</span>
        <input
          type="text"
          class="file-search-input"
          placeholder="Search files in this directory..."
          id="file-search-input"
        />
      </div>
      <div class="file-search-results" id="file-search-results"></div>
    `;

    dirHeader.parentNode.insertBefore(searchContainer, dirHeader.nextSibling);

    const searchInput = document.getElementById('file-search-input');
    const searchResults = document.getElementById('file-search-results');
    const table = document.querySelector('.dir-table tbody');

    if (!searchInput || !table) return;

    searchInput.addEventListener('input', () => {
      const searchTerm = searchInput.value.toLowerCase().trim();
      const rows = table.querySelectorAll('.fs-row');
      let visibleCount = 0;
      let totalCount = 0;

      rows.forEach(row => {
        // Skip parent directory row
        if (row.querySelector('.fs-entry.up')) return;

        totalCount++;
        const label = row.querySelector('.fs-label')?.textContent.toLowerCase() || '';

        if (!searchTerm || label.includes(searchTerm)) {
          row.style.display = '';
          visibleCount++;
        } else {
          row.style.display = 'none';
        }
      });

      if (searchTerm) {
        searchResults.textContent = `Showing ${visibleCount} of ${totalCount} items`;
      } else {
        searchResults.textContent = '';
      }
    });

    // Clear search on Esc
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        searchInput.value = '';
        searchInput.dispatchEvent(new Event('input'));
        searchInput.blur();
      }
    });
  }

  // === KEYBOARD SHORTCUTS ===
  function initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      // Don't trigger if typing in an input
      if (e.target.matches('input, textarea')) return;

      // Ctrl/Cmd + F for file search
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        const fileSearch = document.getElementById('file-search-input');
        if (fileSearch) {
          e.preventDefault();
          fileSearch.focus();
        }
      }
    });
  }

  // === INITIALIZATION ===
  function init() {
    initDragAndDrop();
    initContextMenus();
    initFileSearch();
    initKeyboardShortcuts();
  }

  // Run on page load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Re-run after HTMX swaps (for file browser updates)
  document.body.addEventListener('htmx:afterSwap', (e) => {
    if (e.detail.target.id === 'browser' || e.detail.target.id === 'file-browser') {
      init();
    }
  });
})();
