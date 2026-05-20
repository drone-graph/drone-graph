"""End-to-end test: AuthenticatedChrome.start_managed with Profile 2."""
from __future__ import annotations

import subprocess
import sys
import time

import urllib.request
import urllib.error


def probe_cdp(port: int) -> bool:
    try:
        url = f"http://127.0.0.1:{port}/json/version"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


# 1. Kill any existing Chrome
print("Killing existing Chrome processes...", flush=True)
subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, timeout=10)
time.sleep(1)

# 2. Import and test
sys.path.insert(0, "src")
from drone_graph.tools.builtins.browser.authenticated.chrome_launcher import (
    AuthenticatedChrome,
    _resolve_cdp_user_data,
    _probe_cdp,
)
from drone_graph.tools.builtins.browser.authenticated.config import load_config
from pathlib import Path

config = load_config()
profile_dir = Path(r"C:\Users\Abhinav\AppData\Local\Google\Chrome\User Data\Profile 2")

# Verify the CDP port is free
print(f"CDP port {config.cdp_port} free before start: {not _probe_cdp(config.cdp_port)}", flush=True)

# Start managed Chrome
print("\nCalling AuthenticatedChrome.start_managed()...", flush=True)
result = AuthenticatedChrome.start_managed(config, profile_dir)
print(f"start_managed returned: {result}", flush=True)

# Check CDP
time.sleep(1)
cdp_ready = _probe_cdp(config.cdp_port)
print(f"CDP ready: {cdp_ready}", flush=True)

# Stop
AuthenticatedChrome.stop_managed()
time.sleep(0.5)
cdp_after = _probe_cdp(config.cdp_port)
print(f"CDP after stop: {cdp_after}", flush=True)

print(f"\n{'='*50}", flush=True)
print(f"OVERALL: {'PASS' if cdp_ready else 'FAIL'}", flush=True)
sys.exit(0 if cdp_ready else 1)
