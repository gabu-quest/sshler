#!/usr/bin/env python3
"""
Test script to verify the sshler fixes:
1. Multi-terminal navigation link
2. Token handling improvements  
3. Terminal WebSocket connection fixes
"""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

def check_frontend_build():
    """Check if frontend is built and contains our fixes."""
    dist_path = Path("sshler/static/dist")
    if not dist_path.exists():
        print("❌ Frontend dist not found - run 'cd frontend && pnpm run build'")
        return False
    
    # Check if index.html exists
    index_html = dist_path / "index.html"
    if not index_html.exists():
        print("❌ Frontend index.html not found")
        return False
    
    print("✅ Frontend build found")
    return True

def check_multi_terminal_route():
    """Check if multi-terminal route exists in the built frontend."""
    try:
        # Look for multi-terminal in the built JS files
        dist_path = Path("sshler/static/dist/assets")
        js_files = list(dist_path.glob("*.js"))
        
        found_multi_terminal = False
        for js_file in js_files:
            content = js_file.read_text()
            if "multi-terminal" in content or "Multi-Terminal" in content:
                found_multi_terminal = True
                break
        
        if found_multi_terminal:
            print("✅ Multi-terminal route found in built frontend")
            return True
        else:
            print("❌ Multi-terminal route not found in built frontend")
            return False
    except Exception as e:
        print(f"❌ Error checking multi-terminal route: {e}")
        return False

def check_source_fixes():
    """Check if our source code fixes are in place."""
    fixes_found = 0
    total_fixes = 3
    
    # Check 1: Multi-terminal link in AppHeader.vue
    try:
        header_content = Path("frontend/src/components/AppHeader.vue").read_text()
        if "multi-terminal" in header_content and "Alt+M" in header_content:
            print("✅ Multi-terminal navigation link added to header")
            fixes_found += 1
        else:
            print("❌ Multi-terminal navigation link not found in header")
    except Exception as e:
        print(f"❌ Error checking header: {e}")
    
    # Check 2: Token handling improvements in bootstrap store
    try:
        bootstrap_content = Path("frontend/src/stores/bootstrap.ts").read_text()
        if "refreshInterval" in bootstrap_content and "cleanup" in bootstrap_content:
            print("✅ Token handling improvements found in bootstrap store")
            fixes_found += 1
        else:
            print("❌ Token handling improvements not found")
    except Exception as e:
        print(f"❌ Error checking bootstrap store: {e}")
    
    # Check 3: WebSocket connection fix in Terminal component
    try:
        terminal_content = Path("frontend/src/components/Terminal.vue").read_text()
        if "terminal/handshake" in terminal_content and "handshakeResponse" in terminal_content:
            print("✅ WebSocket connection fix found in Terminal component")
            fixes_found += 1
        else:
            print("❌ WebSocket connection fix not found")
    except Exception as e:
        print(f"❌ Error checking terminal component: {e}")
    
    return fixes_found == total_fixes

def main():
    """Run all checks."""
    print("🔍 Checking sshler fixes...\n")
    
    all_good = True
    
    # Check source code fixes
    if not check_source_fixes():
        all_good = False
    
    print()
    
    # Check frontend build
    if not check_frontend_build():
        all_good = False
    
    # Check multi-terminal route in build
    if not check_multi_terminal_route():
        all_good = False
    
    print("\n" + "="*50)
    
    if all_good:
        print("🎉 All fixes appear to be in place!")
        print("\nTo test:")
        print("1. Run: sshler serve")
        print("2. Open browser to http://localhost:8822/app")
        print("3. Check for 'Multi-Terminal' link in header")
        print("4. Test terminal connections work over LAN")
        print("5. Verify token issues are resolved")
    else:
        print("❌ Some fixes are missing or incomplete")
        print("\nPlease review the errors above and re-run the fixes")
    
    return 0 if all_good else 1

if __name__ == "__main__":
    sys.exit(main())
