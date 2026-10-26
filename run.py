#!/usr/bin/env python3
"""
Creator Forge Internal Ops — Entry Point
Run: python3 run.py
"""
import os
import sys

# Auto-switch to project virtual environment if not already running under it
venv_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv"))
if os.path.exists(venv_dir) and os.path.abspath(sys.prefix) != venv_dir:
    venv_python = os.path.join(venv_dir, "bin", "python")
    os.execv(venv_python, [venv_python] + sys.argv)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Apply early TLS patch to prevent macOS LibreSSL session ticket double-free crash
import app.ssl_patch

# Load .env if present
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                cleaned_val = val.strip().strip('"\'')
                os.environ[key.strip()] = cleaned_val

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"\n{'='*50}")
    print("  CREATOR FORGE — Internal Ops Pipeline")
    print(f"  http://localhost:{port}")
    print(f"  API docs: http://localhost:{port}/docs")
    print(f"{'='*50}\n")
    reload_flag = os.getenv("RELOAD", "true").lower() in ("true", "1")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=reload_flag,
        reload_dirs=["app"] if reload_flag else None,
        timeout_keep_alive=300,
    )

