# sshler Fixes Applied

## What is the X-SSHLER-TOKEN?

The X-SSHLER-TOKEN is a **CSRF (Cross-Site Request Forgery) protection token** that:
- Is generated randomly each time the sshler server starts
- Prevents malicious websites from making requests to your sshler instance
- Must be included in all API requests for security
- Changes every server restart (this was causing your issues!)

**The Problem**: The frontend was caching old tokens that became invalid when you restarted the server.

## Issues Fixed

### 1. Missing Multi-Terminal Navigation Link ✅

**Problem**: Multi-terminal view existed at `/multi-terminal` route but had no navigation link in the header.

**Solution**: 
- Added "Multi-Terminal" link to navigation in `AppHeader.vue`
- Added `Alt+M` keyboard shortcut for quick access
- Updated shortcuts list in bootstrap store

**Files Modified**:
- `frontend/src/components/AppHeader.vue`
- `frontend/src/stores/bootstrap.ts`

### 2. Token Handling Issues ✅ **[MAJOR FIX]**

**Problem**: 
- Cached tokens became invalid after server restart
- Manual token refresh required in settings
- "Missing or invalid X-SSHLER-TOKEN header" errors

**Solution**:
- **Always fetch fresh token** on app startup (ignores cached token)
- **Validate cached tokens** before using them
- **Aggressive auto-retry** on any 403 error
- **Clear invalid tokens** automatically
- Periodic background refresh every 30 minutes

**Files Modified**:
- `frontend/src/api/http.ts` - Improved retry logic
- `frontend/src/stores/bootstrap.ts` - Always use fresh tokens
- `frontend/src/main.ts` - Token validation on startup

### 3. Terminal WebSocket Connection Issues for LAN Access ✅

**Problem**: Terminal connections failed when accessing sshler over LAN because WebSocket used `window.location.host` instead of backend host.

**Solution**:
- Modified terminal connection to use backend handshake endpoint
- WebSocket URL now comes from `/api/v1/terminal/handshake` which provides correct host
- Added better error handling and user feedback for connection failures
- Added specific error messages for authentication failures

**Files Modified**:
- `frontend/src/components/Terminal.vue`

## Testing the Fixes

1. **Start sshler**:
   ```bash
   sshler serve
   ```

2. **Access the Vue SPA**:
   - Open browser to `http://localhost:8822/app` (or your LAN IP)
   - **No more token errors!** Should work immediately

3. **Verify Multi-Terminal Link**:
   - Check header navigation contains "Multi-Terminal" link
   - Test `Alt+M` keyboard shortcut works

4. **Test Token Handling**:
   - ✅ No manual token refresh needed
   - ✅ Works immediately after server restart
   - ✅ Auto-recovers from token issues
   - ✅ No disruptive page reloads

5. **Test Terminal Over LAN**:
   - Access sshler from another device on LAN
   - Terminal connections should work properly
   - Clear error messages for any connection issues

## Why the Token Exists

The token is **necessary for security** - it prevents other websites from controlling your sshler instance. However, the user experience is now seamless:

- ✅ **Automatic**: No manual intervention needed
- ✅ **Transparent**: Works in background
- ✅ **Resilient**: Auto-recovers from server restarts
- ✅ **Secure**: Still provides CSRF protection

## Build Status

Frontend has been rebuilt with all token fixes:
- ✅ Fresh token fetching on startup
- ✅ Cached token validation
- ✅ Aggressive auto-retry logic
- ✅ Multi-terminal navigation included

**The token issues should now be completely resolved!**
