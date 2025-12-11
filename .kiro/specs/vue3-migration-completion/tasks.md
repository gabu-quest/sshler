# Implementation Plan

- [x] 1. Set up development workflow and CLI enhancements
  - Extend CLI to support `--dev` flag that starts both FastAPI and Vite servers
  - Implement process management for concurrent server startup
  - Add graceful shutdown handling for both processes
  - Configure browser auto-opening for development mode
  - _Requirements: 1.1, 1.4_

- [x] 1.1 Write property test for development server startup
  - **Property 2: Backend auto-reload functionality**
  - **Validates: Requirements 1.3**

- [x] 1.2 Write property test for API proxy functionality
  - **Property 3: API proxy functionality**
  - **Validates: Requirements 1.5**

- [x] 2. Complete Vue 3 frontend core infrastructure
- [x] 2.1 Implement responsive navigation and layout system
  - Create responsive AppHeader component with mobile-first design
  - Implement theme toggle functionality with system preference detection
  - Set up Vue Router with proper navigation guards and meta tags
  - Configure Naive UI theme provider with custom theme variables
  - _Requirements: 2.1, 2.4_

- [x] 2.2 Write property test for theme consistency
  - **Property 5: Theme consistency across components**
  - **Validates: Requirements 2.4**

- [x] 2.3 Write property test for mobile responsiveness
  - **Property 4: Mobile interface responsiveness**
  - **Validates: Requirements 2.3, 6.1, 6.2**

- [x] 2.4 Implement keyboard navigation and accessibility features
  - Add focus management system with visible focus indicators
  - Implement logical tab order across all interactive elements
  - Add ARIA labels and semantic markup for screen readers
  - Configure reduced motion support for accessibility
  - _Requirements: 2.5, 7.1, 7.2, 7.3_

- [x] 2.5 Write property test for keyboard navigation
  - **Property 6: Keyboard navigation accessibility**
  - **Validates: Requirements 2.5, 7.2**

- [x] 2.6 Write property test for screen reader accessibility
  - **Property 21: Screen reader accessibility**
  - **Validates: Requirements 7.1, 7.5**

- [x] 2.7 Write property test for motion preference respect
  - **Property 22: Motion preference respect**
  - **Validates: Requirements 7.3**

- [x] 2.8 Write property test for color contrast compliance
  - **Property 23: Color contrast compliance**
  - **Validates: Requirements 7.4**

- [x] 3. Implement command palette functionality
- [x] 3.1 Create command palette component with search
  - Build CommandPalette.vue with fuzzy search functionality
  - Implement keyboard shortcut handling (Cmd/Ctrl+K)
  - Add action registry system for extensible commands
  - Create modal behavior with proper focus trapping
  - _Requirements: 3.1, 3.2, 3.4_

- [x] 3.2 Write property test for command palette search
  - **Property 7: Command palette search functionality**
  - **Validates: Requirements 3.2**

- [x] 3.3 Implement command palette action execution
  - Add action execution system with proper error handling
  - Implement palette auto-close after action execution
  - Add escape key handling to close palette and restore focus
  - Create command categories and keyboard shortcuts display
  - _Requirements: 3.3, 3.5_

- [x] 3.4 Write property test for action execution
  - **Property 8: Command palette action execution**
  - **Validates: Requirements 3.3**

- [x] 3.5 Write property test for modal behavior
  - **Property 9: Command palette modal behavior**
  - **Validates: Requirements 3.4**

- [x] 4. Build file browser and management system
- [x] 4.1 Create file browser core components
  - Implement FilesView with directory navigation
  - Create file/folder display with icons and metadata
  - Add breadcrumb navigation with clickable path segments
  - Implement file selection with multi-select support
  - _Requirements: 4.1, 4.2_

- [x] 4.2 Write property test for directory navigation
  - **Property 10: File browser directory navigation**
  - **Validates: Requirements 4.1**

- [x] 4.3 Write property test for file selection
  - **Property 11: File selection and bulk operations**
  - **Validates: Requirements 4.2**

- [x] 4.4 Implement file upload with drag and drop
  - Create FileUpload component with drag-and-drop zone
  - Add upload progress tracking with cancellation support
  - Implement error handling with retry functionality
  - Add file validation and size limit enforcement
  - _Requirements: 4.3_

- [x] 4.5 Write property test for file upload
  - **Property 12: File upload with progress tracking**
  - **Validates: Requirements 4.3**

- [x] 4.6 Create file operations and context menus
  - Implement context menu component for file actions
  - Add file operations: create, delete, rename, move, copy
  - Create file preview functionality with syntax highlighting
  - Implement real-time file search within directories
  - _Requirements: 4.4, 4.5_

- [x] 4.7 Write property test for file operations
  - **Property 13: File operation persistence**
  - **Validates: Requirements 4.4**

- [x] 4.8 Write property test for file search
  - **Property 14: File search functionality**
  - **Validates: Requirements 4.5**

- [-] 5. Implement terminal interface and WebSocket integration
- [ ] 5.1 Create terminal component with xterm.js integration
  - Build TerminalView component with xterm.js
  - Implement WebSocket connection management
  - Add terminal session creation and management
  - Configure terminal themes and font settings
  - _Requirements: 5.1_

- [ ] 5.2 Write property test for terminal WebSocket connection
  - **Property 15: Terminal WebSocket connection**
  - **Validates: Requirements 5.1, 5.2, 5.3**

- [ ] 5.3 Implement terminal resize and window management
  - Add terminal resize handling with proper dimension updates
  - Implement window switching and session management
  - Add terminal disconnection detection and reconnection
  - Create terminal toolbar with session controls
  - _Requirements: 5.4, 5.5_

- [ ] 5.4 Write property test for terminal resize
  - **Property 16: Terminal resize handling**
  - **Validates: Requirements 5.4**

- [ ] 5.5 Write property test for disconnection handling
  - **Property 17: Terminal disconnection handling**
  - **Validates: Requirements 5.5**

- [ ] 6. Optimize mobile experience and touch interactions
- [ ] 6.1 Implement mobile-specific UI adaptations
  - Add mobile viewport handling and keyboard adjustments
  - Implement touch gesture support (swipe, long press)
  - Create mobile-optimized terminal interface
  - Add device orientation change handling
  - _Requirements: 6.3, 6.4, 6.5_

- [ ] 6.2 Write property test for mobile keyboard adjustment
  - **Property 18: Mobile keyboard viewport adjustment**
  - **Validates: Requirements 6.3**

- [ ] 6.3 Write property test for orientation adaptation
  - **Property 19: Device orientation adaptation**
  - **Validates: Requirements 6.4**

- [ ] 6.4 Write property test for mobile terminal optimization
  - **Property 20: Mobile terminal optimization**
  - **Validates: Requirements 6.5**

- [ ] 7. Implement Pinia stores for state management
- [ ] 7.1 Create core application stores
  - Implement appStore for global state and configuration
  - Create boxesStore for SSH server management
  - Build filesStore for file browser state
  - Implement terminalStore for session management
  - Add localStorage persistence for user preferences
  - _Requirements: 2.1, 4.1, 5.1_

- [ ] 7.2 Write unit tests for Pinia stores
  - Create unit tests for all store actions and getters
  - Test state persistence and hydration
  - Verify store reactivity and computed properties
  - _Requirements: 9.1_

- [ ] 8. Set up production build and static file serving
- [ ] 8.1 Configure production build pipeline
  - Optimize Vite build configuration for production
  - Set up static file serving in FastAPI
  - Implement SPA routing fallback for direct URL access
  - Add caching headers and compression for static assets
  - _Requirements: 8.1, 8.3, 8.4_

- [ ] 8.2 Write property test for SPA routing
  - **Property 24: SPA routing for direct access**
  - **Validates: Requirements 8.4**

- [ ] 8.3 Write property test for static file caching
  - **Property 26: Static file caching headers**
  - **Validates: Requirements 8.3**

- [ ] 8.4 Ensure production deployment without Node.js dependencies
  - Verify built assets are properly included in Python package
  - Test production deployment without Node.js runtime
  - Validate API authentication continues to work correctly
  - _Requirements: 8.2, 8.5_

- [ ] 8.5 Write property test for API authentication
  - **Property 25: API authentication consistency**
  - **Validates: Requirements 8.5**

- [ ] 9. Implement data migration and backward compatibility
- [ ] 9.1 Create data migration system
  - Implement migration for existing SSH configurations
  - Add user preference migration from legacy format
  - Create one-time migration process with user confirmation
  - Ensure no data loss during migration process
  - _Requirements: 10.1, 10.2, 10.4, 10.5_

- [ ] 9.2 Write property test for data migration
  - **Property 28: Data migration preservation**
  - **Validates: Requirements 10.1, 10.2, 10.3, 10.4**

- [ ] 9.3 Preserve user customizations and preferences
  - Maintain existing theme and layout preferences
  - Preserve favorites and bookmarks
  - Keep SSH key configurations and aliases
  - _Requirements: 10.3_

- [ ] 10. Set up comprehensive testing infrastructure
- [ ] 10.1 Configure frontend testing with Vitest
  - Set up Vitest with Vue Testing Library
  - Configure coverage reporting and thresholds
  - Add property-based testing with fast-check
  - Create test utilities and mock factories
  - _Requirements: 9.1_

- [ ] 10.2 Configure backend testing enhancements
  - Enhance existing pytest setup for new API endpoints
  - Add property-based testing with Hypothesis
  - Create WebSocket testing utilities
  - Add performance and load testing
  - _Requirements: 9.2_

- [ ] 10.3 Set up end-to-end testing with Playwright
  - Configure Playwright for cross-browser testing
  - Create E2E tests for complete user workflows
  - Add mobile device simulation tests
  - Implement visual regression testing
  - _Requirements: 9.3_

- [ ] 10.4 Write property test for test execution
  - **Property 27: Test execution and reporting**
  - **Validates: Requirements 9.4, 9.5**

- [ ] 11. Final integration and polish
- [ ] 11.1 Integrate all components and test complete workflows
  - Test complete user journeys from boxes to files to terminal
  - Verify all keyboard shortcuts and accessibility features
  - Test mobile experience across different devices
  - Validate performance and loading times
  - _Requirements: All_

- [ ] 11.2 Write property test for frontend hot reload
  - **Property 1: Frontend hot reload consistency**
  - **Validates: Requirements 1.2**

- [ ] 11.3 Final cleanup and documentation updates
  - Update README with new development workflow
  - Clean up legacy HTMX templates and routes
  - Add migration guide for existing users
  - Update CLI help text and documentation
  - _Requirements: All_

- [ ] 12. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.