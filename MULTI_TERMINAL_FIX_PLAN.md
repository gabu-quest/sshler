# Multi-Terminal & Terminal UX Fix Plan

## Issues to Fix

### 1. Multi-Terminal Layout Issues
- **Problem**: Too much padding, terminals get too small when many are added
- **Solution**: Minimal padding, min-height constraints, dynamic font scaling

### 2. Connection Alert Annoyance  
- **Problem**: "Connected to local, Host: localhost • User: gabu" alert requires manual close
- **Solution**: Replace with auto-dismissing naive-ui message or notification

### 3. Missing Right-Click Context Menu
- **Problem**: No copy/paste context menu like old sshler
- **Solution**: Add right-click menu with copy (if selection) / paste (if no selection)

### 4. Ctrl+C Behavior
- **Problem**: Ctrl+C sends cancel command instead of copying
- **Solution**: Intercept Ctrl+C when text is selected for copy

## Implementation Plan

### Fix 1: Multi-Terminal Layout
```css
.multi-terminal-page {
  padding: 0 4px; /* Minimal horizontal padding */
}

.terminal-grid {
  gap: 2px; /* Smaller gaps */
  min-height: 0;
  overflow-y: auto; /* Allow vertical scrolling */
}

.terminal-container {
  min-height: 300px; /* Minimum height per terminal */
  height: auto; /* Allow growth */
}

/* Dynamic font scaling based on terminal count */
.terminal-wrapper[data-count="1-4"] { font-size: 14px; }
.terminal-wrapper[data-count="5-8"] { font-size: 13px; }
.terminal-wrapper[data-count="9+"] { font-size: 12px; }
```

### Fix 2: Connection Messages
```typescript
// Replace alert with auto-dismissing message
const showConnectionStatus = (boxName: string, host: string, user: string) => {
  message.success(`Connected to ${boxName} (${user}@${host})`, {
    duration: 2000, // Auto-dismiss after 2 seconds
    closable: false
  });
};

// Or use notification for less intrusive feedback
const showConnectionNotification = (boxName: string) => {
  notification.success({
    title: 'Connected',
    content: `Terminal ready on ${boxName}`,
    duration: 1500,
    placement: 'bottom-right'
  });
};
```

### Fix 3: Right-Click Context Menu
```typescript
// Add context menu to terminal
const handleContextMenu = (event: MouseEvent) => {
  event.preventDefault();
  
  const hasSelection = terminal?.hasSelection();
  const menuItems = hasSelection 
    ? [{ label: 'Copy', action: () => copySelection() }]
    : [{ label: 'Paste', action: () => pasteFromClipboard() }];
    
  showContextMenu(event.clientX, event.clientY, menuItems);
};

const copySelection = () => {
  const selection = terminal?.getSelection();
  if (selection) {
    navigator.clipboard.writeText(selection);
    message.success('Copied to clipboard');
  }
};

const pasteFromClipboard = async () => {
  try {
    const text = await navigator.clipboard.readText();
    terminal?.paste(text);
  } catch (err) {
    message.error('Failed to paste from clipboard');
  }
};
```

### Fix 4: Smart Ctrl+C Handling
```typescript
// Intercept keyboard events
const handleKeyDown = (event: KeyboardEvent) => {
  if (event.ctrlKey && event.key === 'c') {
    if (terminal?.hasSelection()) {
      // Copy selection instead of sending interrupt
      event.preventDefault();
      copySelection();
      return;
    }
    // Otherwise, let Ctrl+C send interrupt signal as normal
  }
  
  if (event.ctrlKey && event.key === 'v') {
    event.preventDefault();
    pasteFromClipboard();
  }
};
```

## File Changes Required

### 1. MultiTerminalView.vue
- Reduce padding from `padding: 4px` to `padding: 0 2px`
- Add `overflow-y: auto` to terminal grid
- Implement dynamic font sizing based on terminal count
- Add min-height constraints

### 2. Terminal.vue  
- Remove connection alert/banner
- Add auto-dismissing success message on connect
- Implement right-click context menu
- Add smart Ctrl+C/Ctrl+V handling
- Add selection detection methods

### 3. New ContextMenu.vue Component
- Reusable context menu component
- Position at mouse coordinates
- Handle click outside to close
- Keyboard navigation support

## Priority Order

1. **Multi-terminal layout fixes** (immediate visual improvement)
2. **Connection message replacement** (UX annoyance fix)  
3. **Right-click context menu** (power user feature)
4. **Smart keyboard shortcuts** (workflow improvement)

## Success Criteria

- ✅ Multi-terminal can fit 10+ terminals without becoming unusable
- ✅ No manual dismissal of connection messages required
- ✅ Right-click provides copy/paste like old sshler
- ✅ Ctrl+C copies when text selected, sends interrupt when not
- ✅ Maintains responsive design on mobile
- ✅ Accessible keyboard navigation
