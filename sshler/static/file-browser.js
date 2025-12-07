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
      <div class="context-menu-item" data-action="rename">✍️ Rename</div>
      <div class="context-menu-item" data-action="copy">📄 Copy</div>
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
          case 'rename':
            // Trigger double-click on the name cell to start rename
            const nameCell = fileRow.querySelector('td.file-name');
            if (nameCell) {
              nameCell.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
            }
            break;
          case 'copy':
            // Copy the file
            const copyFilePath = fileRow.dataset.path;
            const boxName = window.location.pathname.split('/')[2];
            const directory = new URLSearchParams(window.location.search).get('path') || '/';
            const token = window.sshlerToken || '';

            if (!copyFilePath) return;

            window.sshlerShowToast?.('Copying...', 'info');

            fetch(`/box/${boxName}/copy`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-SSHLER-TOKEN': token,
              },
              body: new URLSearchParams({
                path: copyFilePath,
                directory: directory,
                target: 'browser',
              }),
            })
            .then(response => response.text())
            .then(html => {
              const browserEl = document.getElementById('browser');
              if (browserEl) {
                browserEl.innerHTML = html;
                init(); // Re-initialize after reload
              }
            })
            .catch(error => {
              console.error('Copy error:', error);
              window.sshlerShowToast?.('Failed to copy file', 'error');
            });
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

  // === BULK SELECTION & ACTIONS ===
  function initBulkSelection() {
    const selectAll = document.getElementById('select-all');
    const bulkActions = document.getElementById('bulk-actions');
    const bulkCount = bulkActions?.querySelector('.bulk-count');
    const bulkDeleteBtn = document.getElementById('bulk-delete-btn');
    const bulkCancelBtn = document.getElementById('bulk-cancel-btn');

    if (!selectAll || !bulkActions) return;

    function getSelectedFiles() {
      return Array.from(document.querySelectorAll('.file-select:checked'));
    }

    function updateBulkActions() {
      const selected = getSelectedFiles();
      const count = selected.length;

      if (count > 0) {
        bulkActions.style.display = 'flex';
        bulkCount.textContent = `${count} file${count === 1 ? '' : 's'} selected`;
      } else {
        bulkActions.style.display = 'none';
        selectAll.checked = false;
      }

      // Update select-all checkbox state
      const allCheckboxes = document.querySelectorAll('.file-select');
      if (allCheckboxes.length > 0) {
        selectAll.checked = count === allCheckboxes.length;
        selectAll.indeterminate = count > 0 && count < allCheckboxes.length;
      }
    }

    // Select all functionality
    selectAll.addEventListener('change', () => {
      const checkboxes = document.querySelectorAll('.file-select');
      checkboxes.forEach(cb => cb.checked = selectAll.checked);
      updateBulkActions();
    });

    // Individual checkbox changes
    document.addEventListener('change', (e) => {
      if (e.target.classList.contains('file-select')) {
        updateBulkActions();
      }
    });

    // Cancel bulk selection
    bulkCancelBtn?.addEventListener('click', () => {
      const checkboxes = document.querySelectorAll('.file-select');
      checkboxes.forEach(cb => cb.checked = false);
      selectAll.checked = false;
      updateBulkActions();
    });

    // Bulk delete
    bulkDeleteBtn?.addEventListener('click', async () => {
      const selected = getSelectedFiles();
      if (selected.length === 0) return;

      const fileNames = selected.map(cb => cb.dataset.fileName).join(', ');
      const confirmMsg = `Delete ${selected.length} file${selected.length === 1 ? '' : 's'}?\n\n${fileNames}`;

      if (!confirm(confirmMsg)) return;

      // Get the current box name and directory from the page
      const boxName = window.location.pathname.split('/')[2];
      const deleteForm = document.querySelector('form[hx-post*="/delete"]');
      const directory = deleteForm?.querySelector('input[name="directory"]')?.value || '/';
      const target = deleteForm?.querySelector('input[name="target"]')?.value || 'browser';

      // Delete each file
      for (const checkbox of selected) {
        const filePath = checkbox.dataset.filePath;

        try {
          const response = await fetch(`/box/${boxName}/delete`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/x-www-form-urlencoded',
              'X-SSHLER-TOKEN': window.sshlerToken || '',
            },
            body: new URLSearchParams({
              path: filePath,
              directory: directory,
              target: target,
            }),
          });

          if (!response.ok) {
            throw new Error(`Failed to delete ${checkbox.dataset.fileName}`);
          }
        } catch (err) {
          console.error('Delete error:', err);
          window.sshlerShowToast?.(err.message, 'error');
        }
      }

      // Reload the directory listing
      const dirUrl = deleteForm?.getAttribute('hx-post').replace('/delete', '/ls');
      if (dirUrl) {
        const targetElement = document.getElementById(target);
        if (targetElement) {
          const response = await fetch(`${dirUrl}?path=${encodeURIComponent(directory)}&target=${target}`);
          const html = await response.text();
          targetElement.innerHTML = html;
          init(); // Re-initialize after reload
        }
      }

      window.sshlerShowToast?.(`Deleted ${selected.length} file${selected.length === 1 ? '' : 's'}`, 'success');
    });
  }

  // === INLINE RENAME ===
  function initInlineRename() {
    const fileTable = document.querySelector('.dir-table');
    if (!fileTable) return;

    // Double-click on filename to rename
    fileTable.addEventListener('dblclick', (e) => {
      const nameCell = e.target.closest('td.file-name');
      if (!nameCell) return;

      const fileRow = nameCell.closest('tr');
      if (!fileRow) return;

      const filePath = fileRow.dataset.path;
      const fileName = fileRow.dataset.name;
      const isDirectory = fileRow.dataset.isDirectory === 'true';

      if (!filePath || !fileName) return;

      // Create inline input
      const nameLink = nameCell.querySelector('.file-name-link');
      if (!nameLink) return;

      const originalText = nameLink.textContent;
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'inline-rename-input';
      input.value = fileName;
      input.style.cssText = 'width: 100%; padding: 4px; border: 2px solid var(--accent); border-radius: 4px; background: var(--input-bg); color: var(--fg);';

      nameCell.innerHTML = '';
      nameCell.appendChild(input);
      input.focus();
      input.select();

      const finishRename = async (save) => {
        const newName = input.value.trim();

        if (!save || !newName || newName === fileName) {
          // Cancel - restore original
          nameCell.innerHTML = '';
          nameCell.appendChild(nameLink);
          return;
        }

        // Perform rename
        const boxName = window.location.pathname.split('/')[2];
        const directory = new URLSearchParams(window.location.search).get('path') || '/';
        const token = window.sshlerToken || '';

        try {
          const formData = new FormData();
          formData.append('path', filePath);
          formData.append('new_name', newName);
          formData.append('directory', directory);
          formData.append('target', 'browser');

          const response = await fetch(`/box/${boxName}/rename`, {
            method: 'POST',
            headers: {
              'X-SSHLER-TOKEN': token,
            },
            body: formData,
          });

          if (!response.ok) {
            throw new Error('Rename failed');
          }

          // Success - reload the directory listing
          const browserEl = document.getElementById('browser');
          if (browserEl && window.htmx) {
            window.htmx.trigger(browserEl, 'reload');
          } else {
            window.location.reload();
          }
        } catch (error) {
          console.error('Rename error:', error);
          window.showToast?.('Failed to rename file', 'error');
          // Restore original
          nameCell.innerHTML = '';
          nameCell.appendChild(nameLink);
        }
      };

      // Handle Enter and Escape keys
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          finishRename(true);
        } else if (e.key === 'Escape') {
          e.preventDefault();
          finishRename(false);
        }
      });

      // Handle blur (click outside)
      input.addEventListener('blur', () => {
        finishRename(false);
      });
    });
  }

  // === INITIALIZATION ===
  function init() {
    initDragAndDrop();
    initContextMenus();
    initFileSearch();
    initKeyboardShortcuts();
    initBulkSelection();
    initInlineRename();
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
