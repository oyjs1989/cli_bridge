#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    print("========================================")
    print("  cli-bridge Docker Entrypoint")
    print("========================================")

    iflow_path = shutil.which("iflow")
    if iflow_path:
        try:
            result = subprocess.run([iflow_path, "--version"], capture_output=True, text=True, timeout=10)
            version = (result.stdout or result.stderr or "installed").strip() or "installed"
        except Exception:
            version = "installed"
        print(f"✅ iflow CLI found: {version}")
    else:
        print("❌ iflow CLI not found!")
        print("   Please check the Docker image build.")
        return 1

    iflow_home = Path("/root/.iflow")
    if iflow_home.exists() and any(iflow_home.iterdir()):
        print("✅ iflow auth data found")
    else:
        print("⚠️  No iflow auth data detected!")
        print("   To login, run interactively:")
        print("   docker run -it -v iflow-auth:/root/.iflow cli-bridge:latest /bin/bash")
        print("   Then run: iflow")
        print("")

    config_path = Path("/root/.cli-bridge/config.json")
    if not config_path.exists():
        print("📝 Initializing default config...")
        try:
            subprocess.run(["cli-bridge", "onboard"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass

    print("")
    print("Starting cli-bridge...")
    print("========================================")

    cmd = ["cli-bridge", *argv]
    completed = subprocess.run(cmd)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
