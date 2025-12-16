# Dashboard UX Redesign Plan

## Current Problem
The dashboard is full of developer notes, technical jargon, and implementation details. Users don't care about "vite pipeline" or "pinia scaffold" - they want to **get stuff done**.

## User Goals & Mental Model

### What Users Actually Want:
1. **Quick access to their servers** - "Connect to my dev box"
2. **See what's running** - "What terminals do I have open?"
3. **Recent activity** - "Where was I working last?"
4. **System status** - "Is everything working?"
5. **Quick actions** - "Start a new session", "Upload files"

### User Types:
- **Developer**: Needs quick access to dev environments
- **DevOps**: Managing multiple servers, monitoring sessions
- **Casual User**: Occasional file transfers, simple commands

## New Dashboard Design

### Hero Section
```
┌─────────────────────────────────────────────────────┐
│  🚀 Welcome back, User                              │
│  Ready to connect? You have 3 servers available    │
│                                                     │
│  [🖥️ Quick Connect] [📁 Browse Files] [⚡ New Terminal] │
└─────────────────────────────────────────────────────┘
```

### Server Grid (Primary Focus)
```
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ 🟢 dev-server   │ │ 🟡 prod-web     │ │ ⚫ backup-box   │
│ 2 active sessions│ │ 1 active session│ │ offline         │
│ Last: 5min ago  │ │ Last: 2hr ago   │ │ Last: 2d ago    │
│                 │ │                 │ │                 │
│ [Connect] [Files]│ │ [Connect] [Files]│ │ [Connect] [Files]│
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

### Activity Stream (Secondary)
```
┌─────────────────────────────────────────────────────┐
│ 📊 Recent Activity                                  │
│                                                     │
│ 🖥️  Opened terminal on dev-server          5min ago │
│ 📁  Uploaded config.json to prod-web       2hr ago │
│ ⚡  Started session 'deploy' on dev-server  4hr ago │
│ 📝  Edited /etc/nginx.conf on prod-web     1d ago  │
└─────────────────────────────────────────────────────┘
```

### Quick Stats (Tertiary)
```
┌─────────────────────────────────────────────────────┐
│ 📈 At a Glance                                      │
│                                                     │
│ 🖥️ 3 Servers    📁 12 Files Today    ⚡ 5 Sessions │
└─────────────────────────────────────────────────────┘
```

## Visual Design Principles

### 1. **Status-First Design**
- Green/Yellow/Red indicators for server status
- Clear visual hierarchy: Available > Busy > Offline
- Immediate visual feedback on connection state

### 2. **Action-Oriented**
- Big, obvious buttons for primary actions
- "Connect", "Browse Files", "New Terminal" front and center
- No more than 2 clicks to common tasks

### 3. **Contextual Information**
- Show what matters: "2 active sessions", "Last used 5min ago"
- Hide technical details: No tokens, versions, or debug info
- Progressive disclosure: Details available on hover/click

### 4. **Personality & Delight**
- Friendly welcome message with user context
- Emoji/icons for visual scanning
- Micro-animations for state changes
- Empty states that guide users

### 5. **Mobile-First**
- Touch-friendly buttons (44px minimum)
- Responsive grid that stacks on mobile
- Swipe gestures for server cards
- Bottom navigation for key actions

## Information Architecture

### Primary Actions (Always Visible)
1. **Quick Connect** - Connect to most recent/favorite server
2. **Browse Files** - File manager for most recent server
3. **New Terminal** - Start fresh terminal session

### Secondary Actions (Contextual)
1. **Add Server** - When no servers configured
2. **Upload Files** - Drag & drop anywhere
3. **Settings** - Gear icon in header

### Tertiary Actions (Progressive Disclosure)
1. **Session Management** - View/manage active sessions
2. **Activity History** - Full activity log
3. **Server Details** - Configuration, logs, etc.

## Content Strategy

### Microcopy
- "Welcome back" instead of "Overview"
- "Ready to connect?" instead of technical status
- "Your servers" instead of "Boxes"
- "Browse files" instead of "File manager"

### Empty States
- No servers: "Add your first server to get started"
- No activity: "Your recent activity will appear here"
- Offline: "Servers are currently offline"

### Error States
- Connection failed: "Can't reach server - check connection"
- Auth failed: "Login expired - click to refresh"
- No permissions: "Contact admin for access"

## Implementation Priority

### Phase 1: Core Dashboard
1. Server status grid with connection buttons
2. Quick action buttons (Connect, Files, Terminal)
3. Basic activity stream

### Phase 2: Enhanced UX
1. Welcome personalization
2. Drag & drop file upload
3. Keyboard shortcuts overlay

### Phase 3: Advanced Features
1. Server health monitoring
2. Session management
3. Advanced activity filtering

## Success Metrics

### User Experience
- Time to first connection < 10 seconds
- 90% of users find their target server immediately
- Zero confusion about primary actions

### Technical
- Dashboard loads in < 2 seconds
- Works perfectly on mobile
- Accessible (WCAG 2.1 AA)

This dashboard should feel like a **control center for your infrastructure**, not a developer tool showcase.
