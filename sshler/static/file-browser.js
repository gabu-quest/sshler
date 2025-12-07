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

  // === DRAG-DROP FILE MOVE ===
  function initFileDragDrop() {
    const table = document.querySelector('.dir-table');
    if (!table) return;

    const rows = table.querySelectorAll('.fs-row');

    rows.forEach(row => {
      // Make all file/folder rows draggable
      if (row.dataset.path) {
        row.setAttribute('draggable', 'true');

        row.addEventListener('dragstart', (e) => {
          const filePath = row.dataset.path;
          const fileName = row.dataset.name;

          e.dataTransfer.effectAllowed = 'move';
          e.dataTransfer.setData('text/plain', filePath);
          e.dataTransfer.setData('application/x-sshler-file', JSON.stringify({
            path: filePath,
            name: fileName,
          }));

          row.classList.add('dragging');
        });

        row.addEventListener('dragend', (e) => {
          row.classList.remove('dragging');
          // Remove all drop-target classes
          document.querySelectorAll('.drop-target').forEach(el => {
            el.classList.remove('drop-target');
          });
        });
      }

      // Make folder rows drop targets
      const isFolder = row.dataset.isDirectory === 'true';
      if (isFolder) {
        row.addEventListener('dragover', (e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
          row.classList.add('drop-target');
        });

        row.addEventListener('dragleave', (e) => {
          // Only remove if we're actually leaving the row
          if (!row.contains(e.relatedTarget)) {
            row.classList.remove('drop-target');
          }
        });

        row.addEventListener('drop', async (e) => {
          e.preventDefault();
          row.classList.remove('drop-target');

          const data = e.dataTransfer.getData('application/x-sshler-file');
          if (!data) return;

          const { path: sourcePath, name: fileName } = JSON.parse(data);
          const destPath = row.dataset.path;

          // Don't allow dropping on itself
          if (sourcePath === destPath) return;

          const boxName = window.location.pathname.split('/')[2];
          const directory = new URLSearchParams(window.location.search).get('path') || '/';
          const token = window.sshlerToken || '';

          window.sshlerShowToast?.(`Moving ${fileName}...`, 'info');

          try {
            const response = await fetch(`/box/${boxName}/move`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-SSHLER-TOKEN': token,
              },
              body: new URLSearchParams({
                path: sourcePath,
                destination_dir: destPath,
                directory: directory,
                target: 'browser',
              }),
            });

            const html = await response.text();
            const browserEl = document.getElementById('browser');
            if (browserEl) {
              browserEl.innerHTML = html;
              init(); // Re-initialize after reload
            }
          } catch (error) {
            console.error('Move error:', error);
            window.sshlerShowToast?.('Failed to move file', 'error');
          }
        });
      }
    });
  }

  // === UPLOAD PROGRESS ===
  function initUploadProgress() {
    const uploadForm = document.querySelector('form[hx-post*="/upload"]');
    if (!uploadForm) return;

    // Intercept form submission to add progress tracking
    uploadForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      e.stopPropagation();

      const fileInput = uploadForm.querySelector('input[type="file"]');
      const file = fileInput?.files?.[0];

      if (!file) return;

      // Only show progress for files >100KB
      if (file.size < 100 * 1024) {
        // For small files, use default HTMX submission
        uploadForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: false }));
        return;
      }

      const formData = new FormData(uploadForm);
      const boxName = window.location.pathname.split('/')[2];
      const directory = uploadForm.querySelector('input[name="directory"]').value;
      const target = uploadForm.querySelector('input[name="target"]').value;

      // Create progress UI
      const progressContainer = document.createElement('div');
      progressContainer.className = 'upload-progress-container';
      progressContainer.innerHTML = `
        <div class="upload-progress-header">
          <span class="upload-filename">${file.name}</span>
          <button class="upload-cancel-btn" title="Cancel upload">✕</button>
        </div>
        <div class="upload-progress-bar">
          <div class="upload-progress-fill" style="width: 0%"></div>
        </div>
        <div class="upload-progress-details">
          <span class="upload-progress-percent">0%</span>
          <span class="upload-progress-speed">0 KB/s</span>
          <span class="upload-progress-eta">Calculating...</span>
        </div>
      `;

      // Insert before the form
      uploadForm.parentElement.insertBefore(progressContainer, uploadForm);

      const progressFill = progressContainer.querySelector('.upload-progress-fill');
      const progressPercent = progressContainer.querySelector('.upload-progress-percent');
      const progressSpeed = progressContainer.querySelector('.upload-progress-speed');
      const progressEta = progressContainer.querySelector('.upload-progress-eta');
      const cancelBtn = progressContainer.querySelector('.upload-cancel-btn');

      // Upload with progress tracking
      const xhr = new XMLHttpRequest();
      let startTime = Date.now();
      let lastLoaded = 0;
      let lastTime = startTime;

      cancelBtn.addEventListener('click', () => {
        xhr.abort();
        progressContainer.remove();
        window.sshlerShowToast?.('Upload cancelled', 'info');
      });

      xhr.upload.addEventListener('progress', (e) => {
        if (!e.lengthComputable) return;

        const percent = Math.round((e.loaded / e.total) * 100);
        progressFill.style.width = `${percent}%`;
        progressPercent.textContent = `${percent}%`;

        // Calculate speed
        const now = Date.now();
        const timeDiff = (now - lastTime) / 1000; // seconds
        const bytesDiff = e.loaded - lastLoaded;

        if (timeDiff > 0.5) { // Update every 500ms
          const speed = bytesDiff / timeDiff; // bytes per second
          lastLoaded = e.loaded;
          lastTime = now;

          // Format speed
          let speedText;
          if (speed < 1024) {
            speedText = `${speed.toFixed(0)} B/s`;
          } else if (speed < 1024 * 1024) {
            speedText = `${(speed / 1024).toFixed(1)} KB/s`;
          } else {
            speedText = `${(speed / (1024 * 1024)).toFixed(1)} MB/s`;
          }
          progressSpeed.textContent = speedText;

          // Calculate ETA
          const remaining = e.total - e.loaded;
          const eta = remaining / speed; // seconds

          if (eta < 60) {
            progressEta.textContent = `${Math.ceil(eta)}s remaining`;
          } else if (eta < 3600) {
            progressEta.textContent = `${Math.ceil(eta / 60)}m remaining`;
          } else {
            progressEta.textContent = `${Math.ceil(eta / 3600)}h remaining`;
          }
        }
      });

      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          progressContainer.remove();
          // Update the file browser with the response
          const browserEl = document.getElementById(target);
          if (browserEl) {
            browserEl.innerHTML = xhr.responseText;
            init(); // Re-initialize
          }
          window.sshlerShowToast?.(`Uploaded ${file.name}`, 'success');
          fileInput.value = ''; // Clear the input
        } else {
          progressContainer.remove();
          window.sshlerShowToast?.('Upload failed', 'error');
        }
      });

      xhr.addEventListener('error', () => {
        progressContainer.remove();
        window.sshlerShowToast?.('Upload failed', 'error');
      });

      xhr.addEventListener('abort', () => {
        progressContainer.remove();
      });

      const token = window.sshlerToken || '';
      xhr.open('POST', `/box/${boxName}/upload`);
      xhr.setRequestHeader('X-SSHLER-TOKEN', token);
      xhr.send(formData);
    });
  }

  // === TOUCH GESTURES ===
  function initTouchGestures() {
    // Only initialize on touch devices
    if (!('ontouchstart' in window)) return;

    const browserContainer = document.querySelector('.dir-table');
    if (!browserContainer) return;

    // Long-press for context menu on file rows
    let longPressTimer = null;
    let touchStartX = 0;
    let touchStartY = 0;
    const longPressDuration = 500; // ms

    browserContainer.addEventListener('touchstart', (e) => {
      const row = e.target.closest('tr[data-path]');
      if (!row) return;

      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;

      longPressTimer = setTimeout(() => {
        // Trigger context menu
        const path = row.dataset.path;
        const isDir = row.dataset.type === 'dir';

        // Vibrate if supported
        if (navigator.vibrate) {
          navigator.vibrate(50);
        }

        // Show custom context menu or native one
        const contextEvent = new MouseEvent('contextmenu', {
          bubbles: true,
          cancelable: true,
          view: window,
          clientX: touchStartX,
          clientY: touchStartY
        });
        row.dispatchEvent(contextEvent);
      }, longPressDuration);
    });

    browserContainer.addEventListener('touchmove', (e) => {
      // Cancel long-press if finger moves too much
      if (longPressTimer) {
        const touch = e.touches[0];
        const deltaX = Math.abs(touch.clientX - touchStartX);
        const deltaY = Math.abs(touch.clientY - touchStartY);

        if (deltaX > 10 || deltaY > 10) {
          clearTimeout(longPressTimer);
          longPressTimer = null;
        }
      }
    });

    browserContainer.addEventListener('touchend', () => {
      if (longPressTimer) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
      }
    });

    browserContainer.addEventListener('touchcancel', () => {
      if (longPressTimer) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
      }
    });
  }

  function initPullToRefresh() {
    // Only on touch devices
    if (!('ontouchstart' in window)) return;

    const container = document.querySelector('.container');
    if (!container) return;

    let startY = 0;
    let pullDistance = 0;
    const threshold = 80;
    let isPulling = false;

    const pullIndicator = document.createElement('div');
    pullIndicator.className = 'pull-to-refresh-indicator';
    pullIndicator.innerHTML = '<span>↓ Pull to refresh</span>';
    pullIndicator.style.cssText = `
      position: absolute;
      top: -60px;
      left: 0;
      right: 0;
      height: 60px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--accent);
      font-size: 14px;
      transition: transform 0.2s;
      z-index: 1000;
    `;
    container.style.position = 'relative';
    container.insertBefore(pullIndicator, container.firstChild);

    container.addEventListener('touchstart', (e) => {
      if (window.scrollY === 0) {
        startY = e.touches[0].clientY;
        isPulling = true;
      }
    }, { passive: true });

    container.addEventListener('touchmove', (e) => {
      if (!isPulling) return;

      const currentY = e.touches[0].clientY;
      pullDistance = currentY - startY;

      if (pullDistance > 0 && pullDistance < threshold * 1.5) {
        pullIndicator.style.transform = `translateY(${Math.min(pullDistance, threshold)}px)`;

        if (pullDistance >= threshold) {
          pullIndicator.innerHTML = '<span>↑ Release to refresh</span>';
        } else {
          pullIndicator.innerHTML = '<span>↓ Pull to refresh</span>';
        }
      }
    }, { passive: true });

    container.addEventListener('touchend', () => {
      if (isPulling && pullDistance >= threshold) {
        // Trigger refresh
        pullIndicator.innerHTML = '<span>⟳ Refreshing...</span>';

        // Refresh the current directory
        setTimeout(() => {
          window.location.reload();
        }, 300);
      }

      // Reset
      pullIndicator.style.transform = 'translateY(0)';
      isPulling = false;
      pullDistance = 0;

      setTimeout(() => {
        pullIndicator.innerHTML = '<span>↓ Pull to refresh</span>';
      }, 300);
    });
  }

  function initSwipeGestures() {
    // Only on touch devices
    if (!('ontouchstart' in window)) return;

    const container = document.querySelector('.container');
    if (!container) return;

    let startX = 0;
    let startY = 0;
    const swipeThreshold = 100;

    container.addEventListener('touchstart', (e) => {
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
    }, { passive: true });

    container.addEventListener('touchend', (e) => {
      const endX = e.changedTouches[0].clientX;
      const endY = e.changedTouches[0].clientY;

      const deltaX = endX - startX;
      const deltaY = endY - startY;

      // Only horizontal swipes (and mostly horizontal)
      if (Math.abs(deltaX) > swipeThreshold && Math.abs(deltaX) > Math.abs(deltaY) * 2) {
        if (deltaX > 0) {
          // Swipe right - go back/up directory
          const backLink = document.querySelector('.breadcrumb a:last-of-type');
          if (backLink) {
            backLink.click();
          }
        }
        // Note: Swipe left could be used for forward navigation if history is implemented
      }
    }, { passive: true });
  }

  // === INITIALIZATION ===
  function init() {
    initDragAndDrop();
    initContextMenus();
    initFileSearch();
    initKeyboardShortcuts();
    initBulkSelection();
    initInlineRename();
    initFileDragDrop();
    initUploadProgress();
    initTouchGestures();
    initPullToRefresh();
    initSwipeGestures();
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
