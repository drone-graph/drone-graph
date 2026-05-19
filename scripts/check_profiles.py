"""Quick script to inspect profile sizes and detect which have real session data."""
from pathlib import Path

root = Path.home() / ".config" / "drone-graph" / "browser-profiles"
if not root.exists():
    print("No profiles directory found.")
    exit(0)

for d in sorted(root.iterdir()):
    if not d.is_dir():
        continue
    files = list(d.rglob("*"))
    total_bytes = sum(f.stat().st_size for f in files if f.is_file())
    # Profiles with Cookies or Login Data files likely have real sessions
    has_cookies = (d / "Default" / "Cookies").exists() or (d / "Cookies").exists()
    has_logindata = (d / "Default" / "Login Data").exists() or (d / "Login Data").exists()
    status = ""
    if total_bytes > 1_000_000:
        status = "HAS_SESSION_DATA"
    elif total_bytes > 10_000:
        status = "MINIMAL"
    else:
        status = "EMPTY"
    print(f"{d.name:30s} {len(files):5d} files  {total_bytes:>10,} bytes  [{status}]")
