// Command Palette (Cmd/Ctrl+K)
// Provides quick access to all sshler actions

(function() {
  const COMMANDS = [
    // Navigation
    { id: 'nav-boxes', name: 'Go to Boxes', icon: '📦', action: () => window.location.href = '/boxes' },
    { id: 'nav-add-box', name: 'Add New Box', icon: '➕', action: () => window.location.href = '/boxes/new' },

    // Theme
    { id: 'theme-toggle', name: 'Toggle Light/Dark Theme', icon: '🌓', action: () => {
      const btn = document.getElementById('theme-toggle');
      if (btn) btn.click();
    }},

    // Language
    { id: 'lang-toggle', name: 'Switch Language', icon: '🌐', action: () => {
      const btn = document.getElementById('lang-toggle');
      if (btn) btn.click();
    }},

    // Docs
    { id: 'open-docs', name: 'Open Documentation', icon: '📚', action: () => {
      const btn = document.getElementById('docs-btn');
      if (btn) btn.click();
    }},

    // Keyboard shortcuts
    { id: 'show-shortcuts', name: 'Show Keyboard Shortcuts', icon: '⌨️', action: () => {
      // Trigger the keyboard shortcuts modal (? key)
      document.dispatchEvent(new KeyboardEvent('keydown', { key: '?' }));
    }},

    // File operations (when in file browser)
    { id: 'file-new', name: 'Create New File', icon: '📄', condition: () => document.querySelector('[name="filename"]'), action: () => {
      const input = document.querySelector('[name="filename"]');
      if (input) {
        input.focus();
        input.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }},

    { id: 'file-upload', name: 'Upload File', icon: '📤', condition: () => document.querySelector('input[type="file"]'), action: () => {
      const input = document.querySelector('input[type="file"]');
      if (input) input.click();
    }},

    { id: 'file-search', name: 'Search Files', icon: '🔍', condition: () => document.getElementById('file-search-input'), action: () => {
      const input = document.getElementById('file-search-input');
      if (input) {
        input.focus();
        input.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }},

    // Terminal operations (when in terminal)
    { id: 'term-search', name: 'Search in Terminal', icon: '🔍', condition: () => document.getElementById('term-search-btn'), action: () => {
      const btn = document.getElementById('term-search-btn');
      if (btn) btn.click();
    }},

    { id: 'term-font-increase', name: 'Increase Terminal Font', icon: 'A+', condition: () => document.getElementById('term-font-increase'), action: () => {
      const btn = document.getElementById('term-font-increase');
      if (btn) btn.click();
    }},

    { id: 'term-font-decrease', name: 'Decrease Terminal Font', icon: 'A−', condition: () => document.getElementById('term-font-decrease'), action: () => {
      const btn = document.getElementById('term-font-decrease');
      if (btn) btn.click();
    }},

    { id: 'term-theme', name: 'Change Terminal Theme', icon: '🎨', condition: () => document.getElementById('term-theme-btn'), action: () => {
      const btn = document.getElementById('term-theme-btn');
      if (btn) btn.click();
    }},

    { id: 'term-export', name: 'Export Terminal Output', icon: '💾', condition: () => document.getElementById('term-export-btn'), action: () => {
      const btn = document.getElementById('term-export-btn');
      if (btn) btn.click();
    }},

    { id: 'sessions-manager', name: 'Open Session Manager', icon: '📋', condition: () => document.getElementById('session-manager-btn'), action: () => {
      const btn = document.getElementById('session-manager-btn');
      if (btn) btn.click();
    }},
  ];

  let commandPaletteVisible = false;
  let selectedIndex = 0;
  let filteredCommands = [];

  function fuzzyMatch(search, text) {
    search = search.toLowerCase();
    text = text.toLowerCase();

    let searchIdx = 0;
    let score = 0;

    for (let i = 0; i < text.length && searchIdx < search.length; i++) {
      if (text[i] === search[searchIdx]) {
        score += (text.length - i); // Bonus for earlier matches
        searchIdx++;
      }
    }

    return searchIdx === search.length ? score : 0;
  }

  function getAvailableCommands() {
    return COMMANDS.filter(cmd => !cmd.condition || cmd.condition());
  }

  function filterCommands(searchTerm) {
    const available = getAvailableCommands();

    if (!searchTerm.trim()) {
      return available;
    }

    return available
      .map(cmd => ({
        ...cmd,
        score: fuzzyMatch(searchTerm, cmd.name)
      }))
      .filter(cmd => cmd.score > 0)
      .sort((a, b) => b.score - a.score);
  }

  function renderCommandPalette() {
    const modal = document.getElementById('command-palette-modal');
    if (!modal) return;

    const input = modal.querySelector('#command-palette-input');
    const list = modal.querySelector('#command-palette-list');

    if (!list) return;

    list.innerHTML = '';

    if (filteredCommands.length === 0) {
      list.innerHTML = '<div class="command-palette-empty">No commands found</div>';
      return;
    }

    filteredCommands.forEach((cmd, index) => {
      const item = document.createElement('div');
      item.className = 'command-palette-item' + (index === selectedIndex ? ' selected' : '');
      item.innerHTML = `
        <span class="command-icon">${cmd.icon}</span>
        <span class="command-name">${cmd.name}</span>
      `;

      item.addEventListener('click', () => {
        executeCommand(cmd);
      });

      list.appendChild(item);
    });
  }

  function executeCommand(cmd) {
    hideCommandPalette();
    setTimeout(() => {
      cmd.action();
    }, 100);
  }

  function showCommandPalette() {
    if (commandPaletteVisible) return;

    let modal = document.getElementById('command-palette-modal');

    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'command-palette-modal';
      modal.className = 'command-palette-modal';
      modal.innerHTML = `
        <div class="command-palette-backdrop"></div>
        <div class="command-palette-container">
          <div class="command-palette-search">
            <span class="command-palette-search-icon">⌘</span>
            <input type="text" id="command-palette-input" placeholder="Type a command..." autofocus>
          </div>
          <div class="command-palette-list" id="command-palette-list"></div>
          <div class="command-palette-footer">
            <span><kbd>↑↓</kbd> Navigate</span>
            <span><kbd>Enter</kbd> Execute</span>
            <span><kbd>Esc</kbd> Close</span>
          </div>
        </div>
      `;
      document.body.appendChild(modal);

      const input = modal.querySelector('#command-palette-input');
      input.addEventListener('input', (e) => {
        selectedIndex = 0;
        filteredCommands = filterCommands(e.target.value);
        renderCommandPalette();
      });

      input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          selectedIndex = Math.min(selectedIndex + 1, filteredCommands.length - 1);
          renderCommandPalette();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          selectedIndex = Math.max(selectedIndex - 1, 0);
          renderCommandPalette();
        } else if (e.key === 'Enter' && filteredCommands[selectedIndex]) {
          e.preventDefault();
          executeCommand(filteredCommands[selectedIndex]);
        } else if (e.key === 'Escape') {
          e.preventDefault();
          hideCommandPalette();
        }
      });

      modal.querySelector('.command-palette-backdrop').addEventListener('click', () => {
        hideCommandPalette();
      });
    }

    selectedIndex = 0;
    filteredCommands = filterCommands('');
    renderCommandPalette();

    modal.classList.add('visible');
    commandPaletteVisible = true;

    const input = modal.querySelector('#command-palette-input');
    input.value = '';
    input.focus();
  }

  function hideCommandPalette() {
    const modal = document.getElementById('command-palette-modal');
    if (modal) {
      modal.classList.remove('visible');
    }
    commandPaletteVisible = false;
  }

  // Global keyboard shortcut
  document.addEventListener('keydown', (e) => {
    // Cmd+K (Mac) or Ctrl+K (Windows/Linux)
    if ((e.metaKey || e.ctrlKey) && e.key === 'k' && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      showCommandPalette();
    }
  });

  // Export for other scripts
  window.showCommandPalette = showCommandPalette;
})();
