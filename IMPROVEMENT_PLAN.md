# Sshler Improvement Plan

**Generated**: 2025-12-18
**Status**: In Progress

## Progress Overview
- [ ] Critical Security Issues (0/4)
- [ ] High Priority (0/6)
- [ ] Medium Priority (0/8)
- [ ] Low Priority (0/5)

---

## 🔴 CRITICAL SECURITY ISSUES

### 1. [ ] Secure .env File Handling
**Priority**: CRITICAL
**File**: `.env` (root)
**Issue**: Password hash in untracked file could be committed to git
**Actions**:
- [ ] Add `.env` to `.gitignore`
- [ ] Create `.env.example` template
- [ ] Document in README security section
- [ ] Verify no secrets in git history

### 2. [x] Fix Path Traversal via Symlinks
**Priority**: CRITICAL
**File**: `sshler/api/helpers.py:53`
**Issue**: `_normalize_local_path()` resolved symlinks but didn't validate escape
**Risk**: Directory escape attacks via symlinks
**Status**: ✅ COMPLETED
**Actions**:
- [x] Update `_normalize_local_path()` to use `Path.resolve(strict=False)`
- [x] Add optional `allowed_base` parameter for path restriction validation
- [x] Add unit tests for symlink attack scenarios (12 tests in test_path_validation.py)
- [x] Test with actual symlinks - all tests pass
**Notes**:
- Function now accepts optional `allowed_base` parameter for validation
- Defaults to no restriction (None) for backward compatibility
- When `allowed_base` is set, validates resolved path stays within base
- Handles Python 3.8/3.9+ compatibility for `is_relative_to()`
- 12 comprehensive tests cover all attack vectors

### 3. [x] Add Rate Limiting to Critical Endpoints
**Priority**: CRITICAL
**Files**: `sshler/api/rate_limiting.py` (new), `sshler/api/files.py`, `sshler/api/auth.py`
**Issue**: Critical endpoints lacked rate limiting protection
**Status**: ✅ COMPLETED
**Actions**:
- [x] Created `sshler/api/rate_limiting.py` with FastAPI dependency pattern
- [x] Add rate limiting to file upload endpoint (10 req/min)
- [x] Add rate limiting to file delete endpoint (20 req/min)
- [x] Add rate limiting to file write endpoint (30 req/min)
- [x] Add rate limiting to login endpoint (5 req/min - strict for security)
- [x] Create comprehensive tests in `tests/test_rate_limit.py` (15 tests)
- [ ] Document rate limits in README (optional for later)
**Notes**:
- Created reusable FastAPI dependency pattern for rate limiting
- Pre-configured rate limiters for common operations
- Independent rate limits per client IP
- Token bucket algorithm with burst capacity
- All tests pass (15 rate limiting tests + all existing tests)

### 4. [ ] Validate Command Injection Points
**Priority**: CRITICAL
**File**: `sshler/webapp.py:392-436`
**Issue**: Session names passed to tmux subprocess without validation
**Actions**:
- [ ] Add session name sanitization (alphanumeric + dashes only)
- [ ] Create `PathValidator.sanitize_session_name()` method
- [ ] Add unit tests for injection attempts
- [ ] Audit all subprocess calls for similar issues

---

## 🟡 HIGH PRIORITY

### 5. [ ] Add Database Composite Indexes
**Priority**: HIGH
**File**: `sshler/state.py:100-103`
**Issue**: Missing indexes for common queries
**Performance Impact**: O(n) session lookups
**Actions**:
- [ ] Add composite index on `(box, session_name)`
- [ ] Add composite index on `(active, last_accessed_at)` for cleanup
- [ ] Benchmark query performance before/after
- [ ] Document index strategy in code comments

### 6. [ ] Replace Generic Exception Handlers
**Priority**: HIGH
**Files**: `webapp.py` (42), `ssh_pool.py` (6), `state.py` (1)
**Issue**: 76 instances of `except Exception: pass`
**Actions**:
- [ ] Audit all 76 exception handlers
- [ ] Replace webapp.py handlers (42 instances)
- [ ] Replace ssh_pool.py handlers (6 instances)
- [ ] Replace state.py handler (1 instance)
- [ ] Add proper logging to all exception handlers
- [ ] Create custom exception hierarchy if needed

### 7. [ ] Improve Type Hints Coverage
**Priority**: HIGH
**Current**: ~54% coverage
**Target**: 90%+
**Actions**:
- [ ] Add return type hints to `webapp.py:392-436`
- [ ] Add return type hints to `cli.py` functions
- [ ] Add type hints to all public API functions
- [ ] Run mypy --strict and fix issues
- [ ] Add mypy to CI/CD pipeline

### 8. [ ] Add Missing Unit Tests
**Priority**: HIGH
**Modules without tests**:
- [ ] `sshler/validation.py` - Create `tests/test_validation.py`
- [ ] `sshler/rate_limit.py` - Create `tests/test_rate_limit.py`
- [ ] `sshler/ssh_pool.py` - Create `tests/test_ssh_pool.py`
**Target**: 80%+ coverage
**Actions**:
- [ ] Run coverage report to baseline current coverage
- [ ] Write test_validation.py with edge cases
- [ ] Write test_rate_limit.py with concurrency tests
- [ ] Write test_ssh_pool.py with connection lifecycle tests
- [ ] Add coverage reporting to CI/CD

### 9. [ ] Add Health Check Endpoint
**Priority**: HIGH
**Impact**: Production monitoring capability
**Actions**:
- [ ] Create `/api/v1/health` endpoint
- [ ] Return: status, version, uptime, session count
- [ ] Add pool connection stats
- [ ] Add disk space check for config directory
- [ ] Add to API documentation
- [ ] Test in Playwright e2e tests

### 10. [ ] Extract Magic Numbers to Constants
**Priority**: HIGH
**Files**: `session.py`, `ssh_pool.py`, `rate_limit.py`
**Actions**:
- [ ] `session.py:97` - Extract `SESSION_ID_BYTES = 16`
- [ ] `ssh_pool.py:66` - Extract `CLEANUP_INTERVAL_SECONDS = 60`
- [ ] `rate_limit.py` - Extract default TTL as `DEFAULT_SESSION_TTL = 8 * 3600`
- [ ] Create `sshler/constants.py` for shared constants
- [ ] Update all references

---

## 🟢 MEDIUM PRIORITY

### 11. [ ] Split Monolithic webapp.py
**Priority**: MEDIUM
**File**: `sshler/webapp.py` (28,859 tokens)
**Issue**: File too large to analyze in one pass
**Actions**:
- [ ] Create `sshler/webapp/` directory
- [ ] Extract to `webapp/app.py` - Application factory
- [ ] Extract to `webapp/middleware.py` - CORS, auth middleware
- [ ] Extract to `webapp/routes.py` - Legacy route handlers
- [ ] Extract to `webapp/websockets.py` - WebSocket handlers
- [ ] Update imports throughout codebase
- [ ] Verify all tests pass after refactor

### 12. [ ] Implement Graceful Shutdown
**Priority**: MEDIUM
**File**: `sshler/cli.py:162-168`
**Issue**: Signal handlers don't close SSH connections
**Actions**:
- [ ] Implement FastAPI lifespan context manager
- [ ] Add `shutdown_pool()` to ssh_pool.py
- [ ] Add `session_store.clear()` on shutdown
- [ ] Close all active WebSocket connections
- [ ] Log shutdown progress
- [ ] Test with systemd service

### 13. [ ] Add SSH Config Caching
**Priority**: MEDIUM
**File**: `sshler/ssh_config.py`
**Issue**: Re-parses config on every load
**Actions**:
- [ ] Add `@lru_cache` to SSH config parser
- [ ] Key cache by (path, mtime) for invalidation
- [ ] Add cache stats to health endpoint
- [ ] Benchmark before/after performance
- [ ] Add cache clear method for testing

### 14. [ ] Improve Error Context
**Priority**: MEDIUM
**File**: `sshler/api/files.py:89-90`
**Issue**: Generic error messages lack debugging context
**Actions**:
- [ ] Add file paths to error messages
- [ ] Add operation type (read/write/delete) to errors
- [ ] Add box name to SSH errors
- [ ] Create structured error response format
- [ ] Update API error documentation

### 15. [ ] Add Structured Logging
**Priority**: MEDIUM
**Current**: Only 4 logger calls in entire codebase
**Actions**:
- [ ] Add structlog dependency
- [ ] Configure JSON logging for production
- [ ] Add logging to all state changes (session create/delete)
- [ ] Add logging to all auth events (login/logout/failure)
- [ ] Add logging to file operations (upload/delete/edit)
- [ ] Add logging to SSH connection lifecycle
- [ ] Configure log rotation

### 16. [ ] Use Async Context Managers
**Priority**: MEDIUM
**File**: `sshler/ssh.py:158-203`
**Issue**: Manual cleanup in finally blocks
**Actions**:
- [ ] Refactor `sftp_list_directory()` to use async with
- [ ] Refactor all SFTP operations to use context managers
- [ ] Audit all resource cleanup code
- [ ] Add tests for resource cleanup on exceptions

### 17. [ ] Standardize on pathlib.Path
**Priority**: MEDIUM
**Issue**: Mix of string paths and Path objects
**Actions**:
- [ ] Audit all file path operations
- [ ] Convert to pathlib.Path internally
- [ ] Convert to str only at I/O boundaries
- [ ] Update type hints to use Path where appropriate

### 18. [ ] Add Security Scanning to CI/CD
**Priority**: MEDIUM
**File**: `.github/workflows/ci.yml` (if exists)
**Actions**:
- [ ] Add bandit security scanner
- [ ] Add safety dependency checker
- [ ] Add coverage reporting (codecov or coveralls)
- [ ] Configure to fail on HIGH severity issues
- [ ] Add badge to README

---

## 🔵 LOW PRIORITY (Nice to Have)

### 19. [ ] Add __all__ Exports
**Priority**: LOW
**Impact**: Clearer public API surface
**Actions**:
- [ ] Add `__all__` to `sshler/__init__.py`
- [ ] Add `__all__` to `sshler/api/__init__.py`
- [ ] Add `__all__` to all public modules
- [ ] Verify with `import *` tests

### 20. [ ] Use Python 3.10+ match Statements
**Priority**: LOW
**File**: `sshler/cli.py:579-686`
**Issue**: Long if-elif chains
**Actions**:
- [ ] Refactor CLI command dispatch to use match
- [ ] Verify Python 3.12+ is min version (already is)
- [ ] Update other if-elif chains where appropriate

### 21. [ ] Add Property-Based Tests
**Priority**: LOW
**Dependency**: hypothesis (already in dev deps)
**Actions**:
- [ ] Add property tests for path validation
- [ ] Add property tests for session ID generation
- [ ] Add property tests for rate limiting
- [ ] Document hypothesis usage in dev docs

### 22. [ ] Add E2E Edge Cases
**Priority**: LOW
**File**: `tests/e2e/`
**Missing scenarios**:
- [ ] Multi-box concurrent terminal sessions
- [ ] File upload error recovery
- [ ] Session timeout during file transfer
- [ ] Network interruption recovery
- [ ] Unicode/emoji in filenames

### 23. [ ] Add Docker Healthcheck
**Priority**: LOW
**Impact**: Better container orchestration
**Actions**:
- [ ] Add HEALTHCHECK to Dockerfile (if exists)
- [ ] Use /health endpoint
- [ ] Test with docker-compose
- [ ] Document in README

---

## 📝 Notes

### Excluded from Plan (per user request)
- Multi-instance/Redis session backend (not needed now)
- Prometheus metrics (not needed now)

### Testing Strategy
- Run tests after each change: `uv run pytest`
- Check coverage: `uv run pytest --cov=sshler`
- Run e2e tests: `uv run pytest tests/e2e`
- Run linter: `uv run ruff check .`

### Git Workflow
- Create feature branch for each section
- Commit after each change
- PR to main when section complete

---

## 🎯 Current Sprint

**Sprint Goal**: Complete all Critical Security Issues
**Target Date**: Next 2 days

### Today's Focus
1. [ ] Secure .env file handling (#1)
2. [ ] Fix path traversal via symlinks (#2)
3. [ ] Add rate limiting to critical endpoints (#3)

---

**Last Updated**: 2025-12-18
**Next Review**: After completing Critical section
