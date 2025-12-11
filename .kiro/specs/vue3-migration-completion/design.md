# Design Document

## Overview

This design completes the Vue 3 migration for sshler, transforming it from a Jinja/HTMX-based UI to a modern Vue 3 SPA. The migration maintains all existing functionality while providing enhanced UX through Naive UI components, Phosphor Icons, and modern development workflows. The system supports both development mode with hot reloading and production deployment with static file serving.

## Architecture

### High-Level Architecture

The application follows a client-server architecture with clear separation between frontend and backend:

```
┌─────────────────────────────────────────────────────────────┐
│                    Vue 3 Frontend (SPA)                    │
│  ┌─────────────────┐ ┌─────────────────┐ ┌──────────────┐  │
│  │   Vue Router    │ │  Pinia Stores   │ │  Naive UI    │  │
│  │   (Routing)     │ │   (State)       │ │ (Components) │  │
│  └─────────────────┘ └─────────────────┘ └──────────────┘  │
│  ┌─────────────────┐ ┌─────────────────┐ ┌──────────────┐  │
│  │ Command Palette │ │ File Browser    │ │   Terminal   │  │
│  └─────────────────┘ └─────────────────┘ └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │ HTTP/WebSocket
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI Backend Server                   │
│  ┌─────────────────┐ ┌─────────────────┐ ┌──────────────┐  │
│  │   API Routes    │ │  Static Files   │ │  WebSocket   │  │
│  │   (/api/v1)     │ │   (/static)     │ │   (/ws)      │  │
│  └─────────────────┘ └─────────────────┘ └──────────────┘  │
│  ┌─────────────────┐ ┌─────────────────┐ ┌──────────────┐  │
│  │   SSH Pool      │ │  Config Mgmt    │ │ File Ops     │  │
│  └─────────────────┘ └─────────────────┘ └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │ SSH/SFTP
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Remote SSH Servers                      │
└─────────────────────────────────────────────────────────────┘
```

### Development vs Production Architecture

**Development Mode:**
- Vite dev server runs on port 5173 with HMR
- FastAPI server runs on port 8822 with auto-reload
- Vite proxies API requests to FastAPI
- Both servers started with single `--dev` command

**Production Mode:**
- Vue app built to static files in `sshler/static/dist`
- FastAPI serves static files and handles SPA routing
- No Node.js runtime dependency
- Single FastAPI server on port 8822

## Components and Interfaces

### Frontend Components

#### Core Application Structure
- **App.vue**: Root component with theme provider and global layout
- **AppHeader.vue**: Navigation header with theme toggle and command palette trigger
- **Router**: Vue Router configuration for SPA navigation

#### View Components
- **BoxesView**: SSH server management and overview
- **FilesView**: File browser with upload, download, and management
- **TerminalView**: Terminal interface with WebSocket connection
- **SettingsView**: Application configuration and preferences

#### Shared Components
- **CommandPalette**: Keyboard-accessible quick actions (Cmd/Ctrl+K)
- **FileUpload**: Drag-and-drop file upload with progress
- **ContextMenu**: Right-click context menus for file operations
- **NotificationToast**: User feedback for operations

#### Store Architecture (Pinia)
- **appStore**: Global app state, theme, and configuration
- **boxesStore**: SSH server configurations and status
- **filesStore**: File browser state and operations
- **terminalStore**: Terminal sessions and WebSocket management

### Backend API Structure

#### Existing API Endpoints (Already Implemented)
- `GET /api/v1/bootstrap`: Application configuration and authentication
- `GET /api/v1/boxes`: List all SSH servers
- `GET /api/v1/boxes/{name}`: Get specific box details
- `POST /api/v1/boxes/{name}/pin`: Toggle box pinning
- `GET /api/v1/boxes/{name}/files`: List directory contents
- `POST /api/v1/boxes/{name}/files/touch`: Create empty file
- `POST /api/v1/boxes/{name}/files/upload`: Upload files
- `DELETE /api/v1/boxes/{name}/files`: Delete files
- `POST /api/v1/boxes/{name}/files/rename`: Rename files
- `POST /api/v1/boxes/{name}/files/copy`: Copy files
- `POST /api/v1/boxes/{name}/files/move`: Move files
- `GET /api/v1/boxes/{name}/files/preview`: Preview file content
- `GET /api/v1/boxes/{name}/sessions`: List terminal sessions
- `POST /api/v1/boxes/{name}/sessions`: Create terminal session
- `GET /api/v1/terminal/handshake`: Get WebSocket connection info

#### WebSocket Interface
- `WS /ws/term/{box_name}/{session_name}`: Terminal WebSocket connection
- Messages: input, output, resize, window selection, notifications

### CLI Interface Enhancement

#### New Development Command
```bash
uv run sshler serve --dev
```

This command will:
1. Start FastAPI server with `--reload` flag
2. Launch Vite dev server in parallel
3. Open browser to development URL
4. Monitor both processes and restart as needed

#### Implementation Strategy
- Extend `sshler/cli.py` with `--dev` flag
- Use `subprocess` to manage Vite dev server
- Implement process monitoring and cleanup
- Handle graceful shutdown of both servers

## Data Models

### Frontend Data Models (TypeScript)

```typescript
interface Box {
  name: string;
  host: string;
  user: string;
  port: number;
  transport: 'ssh' | 'local';
  pinned: boolean;
  default_dir?: string;
  favorites: string[];
  last_accessed?: number;
}

interface FileEntry {
  name: string;
  path: string;
  is_directory: boolean;
  size?: number;
  modified?: number;
}

interface DirectoryListing {
  box: string;
  directory: string;
  entries: FileEntry[];
}

interface TerminalSession {
  id: string;
  box: string;
  session_name: string;
  working_directory: string;
  created_at: number;
  last_accessed_at: number;
  active: boolean;
  window_count: number;
}

interface AppConfig {
  version: string;
  token_header: string;
  token?: string;
  basic_auth_required: boolean;
  allow_origins: string[];
  spa_base: string;
  spa_enabled: boolean;
}
```

### Backend Data Models (Pydantic)

The backend already has comprehensive Pydantic models in `sshler/api/models.py`:
- `APIBox`: Box representation for API
- `APIDirectoryEntry` & `APIDirectoryListing`: File system data
- `APIFilePreview`: File content preview
- `APISessionInfo` & `APISession`: Terminal session data
- `APIBootstrap`: Application configuration
- Request/response models for all operations

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property Reflection

After reviewing all properties identified in the prework, I've identified several areas where properties can be consolidated:

**Redundancy Analysis:**
- Properties 2.3, 6.1, and 6.2 all test mobile interface behavior and can be combined into a comprehensive mobile responsiveness property
- Properties 2.4 and 2.5 both test UI consistency and can be combined into a comprehensive UI consistency property  
- Properties 4.1, 4.2, 4.4, and 4.5 all test file browser functionality and can be combined into comprehensive file operations properties
- Properties 5.2, 5.3, 5.4, and 5.5 all test terminal functionality and can be combined into comprehensive terminal behavior properties
- Properties 7.1, 7.2, 7.3, 7.4, and 7.5 all test accessibility and can be combined into comprehensive accessibility properties
- Properties 9.1, 9.2, 9.3, 9.4, and 9.5 all test the testing infrastructure itself and can be simplified
- Properties 10.1, 10.2, 10.3, and 10.4 all test data migration and can be combined

**Consolidated Properties:**

Property 1: Frontend hot reload consistency
*For any* Vue component or style file modification during development, the browser should update the component without requiring a full page refresh while preserving application state
**Validates: Requirements 1.2**

Property 2: Backend auto-reload functionality  
*For any* Python file modification during development, the FastAPI server should restart automatically and be ready to serve requests within a reasonable time
**Validates: Requirements 1.3**

Property 3: API proxy functionality
*For any* API request made from the Vue frontend during development, the request should be successfully proxied to the FastAPI backend and return the expected response
**Validates: Requirements 1.5**

Property 4: Mobile interface responsiveness
*For any* screen size below 768px width, all interactive elements should have minimum 44px touch targets and the layout should adapt appropriately for touch interaction
**Validates: Requirements 2.3, 6.1, 6.2**

Property 5: Theme consistency across components
*For any* theme toggle action, all UI components should reflect the new theme consistently without requiring page refresh
**Validates: Requirements 2.4**

Property 6: Keyboard navigation accessibility
*For any* interactive element in the application, keyboard navigation should provide visible focus indicators and follow logical tab order
**Validates: Requirements 2.5, 7.2**

Property 7: Command palette search functionality
*For any* search term entered in the command palette, the results should be filtered using fuzzy matching and return relevant actions
**Validates: Requirements 3.2**

Property 8: Command palette action execution
*For any* action selected from the command palette, the action should execute correctly and the palette should close automatically
**Validates: Requirements 3.3**

Property 9: Command palette modal behavior
*For any* keyboard input while the command palette is open, the input should be captured by the palette and not affect the underlying interface
**Validates: Requirements 3.4**

Property 10: File browser directory navigation
*For any* directory navigation action, the system should display files and folders with appropriate metadata and icons
**Validates: Requirements 4.1**

Property 11: File selection and bulk operations
*For any* multi-file selection, the system should enable appropriate bulk operations and display accurate selection count
**Validates: Requirements 4.2**

Property 12: File upload with progress tracking
*For any* file upload via drag and drop, the system should display upload progress and handle errors gracefully
**Validates: Requirements 4.3**

Property 13: File operation persistence
*For any* file operation (create, delete, rename, move, copy), the changes should be immediately reflected in the UI and persisted to the server
**Validates: Requirements 4.4**

Property 14: File search functionality
*For any* search query in the file browser, the system should return real-time results from the current directory
**Validates: Requirements 4.5**

Property 15: Terminal WebSocket connection
*For any* terminal session creation, a WebSocket connection should be established and provide bidirectional communication
**Validates: Requirements 5.1, 5.2, 5.3**

Property 16: Terminal resize handling
*For any* terminal window resize, the terminal dimensions should be updated and the remote session should be notified of the new size
**Validates: Requirements 5.4**

Property 17: Terminal disconnection handling
*For any* terminal session disconnection, the system should display connection status and provide reconnection options
**Validates: Requirements 5.5**

Property 18: Mobile keyboard viewport adjustment
*For any* mobile device where the virtual keyboard appears, the viewport should adjust to maintain usability of the active input
**Validates: Requirements 6.3**

Property 19: Device orientation adaptation
*For any* device orientation change, the layout should adapt appropriately to the new orientation within a reasonable time
**Validates: Requirements 6.4**

Property 20: Mobile terminal optimization
*For any* terminal usage on mobile devices, the interface should provide optimized keyboard and input experience
**Validates: Requirements 6.5**

Property 21: Screen reader accessibility
*For any* dynamic content change, appropriate ARIA labels and announcements should be provided for screen readers
**Validates: Requirements 7.1, 7.5**

Property 22: Motion preference respect
*For any* user with prefers-reduced-motion setting enabled, animations and transitions should be reduced or disabled
**Validates: Requirements 7.3**

Property 23: Color contrast compliance
*For any* text and background color combination in the interface, the contrast ratio should meet WCAG 2.1 AA standards (4.5:1 for normal text, 3:1 for large text)
**Validates: Requirements 7.4**

Property 24: SPA routing for direct access
*For any* SPA route accessed directly via URL, the system should serve the index.html file and allow client-side routing to handle the navigation
**Validates: Requirements 8.4**

Property 25: API authentication consistency
*For any* API request requiring authentication, the system should validate the X-SSHLER-TOKEN header and return appropriate responses
**Validates: Requirements 8.5**

Property 26: Static file caching headers
*For any* static file request in production, the response should include appropriate caching headers and compression
**Validates: Requirements 8.3**

Property 27: Test execution and reporting
*For any* test suite execution (frontend, backend, or E2E), the system should provide clear feedback on results and detailed error information for failures
**Validates: Requirements 9.4, 9.5**

Property 28: Data migration preservation
*For any* existing user data (SSH configurations, favorites, preferences), the migration process should preserve all data without loss
**Validates: Requirements 10.1, 10.2, 10.3, 10.4**

## Error Handling

### Frontend Error Handling
- **Network Errors**: Retry logic with exponential backoff for API requests
- **WebSocket Disconnections**: Automatic reconnection with user notification
- **File Upload Errors**: Clear error messages with retry options
- **Authentication Errors**: Redirect to login or token refresh
- **Validation Errors**: Inline form validation with helpful messages

### Backend Error Handling
- **SSH Connection Failures**: Graceful degradation with error reporting
- **File System Errors**: Proper error codes and user-friendly messages
- **Rate Limiting**: 429 responses with retry-after headers
- **Validation Errors**: Structured error responses with field-level details
- **WebSocket Errors**: Connection cleanup and client notification

### Error Response Format
```typescript
interface APIError {
  status: 'error';
  message: string;
  code?: string;
  details?: Record<string, any>;
}
```

## Testing Strategy

### Dual Testing Approach

The testing strategy employs both unit testing and property-based testing to ensure comprehensive coverage:

**Unit Testing:**
- Vue component testing with Vue Testing Library
- Pinia store testing with mock data
- FastAPI endpoint testing with TestClient
- WebSocket connection testing
- File operation integration tests

**Property-Based Testing:**
- Use **fast-check** for frontend property tests (JavaScript/TypeScript)
- Use **Hypothesis** for backend property tests (Python)
- Configure each property test to run a minimum of 100 iterations
- Tag each property test with comments referencing design document properties

**Property Test Requirements:**
- Each property-based test must be tagged with: `**Feature: vue3-migration-completion, Property {number}: {property_text}**`
- Each correctness property must be implemented by a single property-based test
- Property tests should focus on universal behaviors that hold across all valid inputs
- Unit tests should cover specific examples, edge cases, and integration points

### Test Categories

**Frontend Tests (Vitest + Vue Testing Library):**
- Component rendering and interaction
- Store state management and mutations
- Router navigation and guards
- API client contract tests
- Accessibility compliance tests

**Backend Tests (pytest + httpx):**
- API endpoint functionality
- WebSocket message handling
- SSH connection pooling
- File operation validation
- Authentication and authorization

**End-to-End Tests (Playwright):**
- Complete user workflows
- Cross-browser compatibility
- Mobile device simulation
- Performance benchmarks
- Visual regression tests

### Test Configuration

**Frontend Test Setup:**
```typescript
// vitest.config.ts
export default defineConfig({
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    coverage: {
      reporter: ['text', 'html', 'lcov'],
      threshold: {
        global: {
          branches: 80,
          functions: 80,
          lines: 80,
          statements: 80
        }
      }
    }
  }
});
```

**Backend Test Setup:**
```python
# pytest configuration
pytest_plugins = ["pytest_asyncio"]
asyncio_mode = "auto"

# Property test configuration
from hypothesis import settings
settings.register_profile("ci", max_examples=100, deadline=None)
settings.load_profile("ci")
```