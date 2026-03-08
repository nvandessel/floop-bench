"""
Progressive validation for mini-SWE-agent integration.

Exits 0 when the integration is ready. Each check builds on the previous.

Usage:
    uv run python -m scripts.validate_mswea
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def check(name: str, fn) -> bool:
    """Run a check and print result."""
    try:
        ok, detail = fn()
        status = "PASS" if ok else "FAIL"
        if not ok:
            print(f"  {status}  {name}: {detail}")
        else:
            print(f"  {status}  {name}")
        return ok
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        return False


def c_uv():
    if shutil.which("uv"):
        return True, ""
    return False, "uv not installed. Run: sudo pacman -S uv"


def c_mini_swe_agent():
    if shutil.which("mini-extra"):
        return True, ""
    # Try importing
    try:
        r = subprocess.run(
            ["uv", "run", "mini-extra", "--help"],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0:
            return True, ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False, "mini-swe-agent not installed. Run: uv add mini-swe-agent"


def c_container_runtime():
    for cmd in ["podman", "docker"]:
        if shutil.which(cmd):
            r = subprocess.run([cmd, "info"], capture_output=True)
            if r.returncode == 0:
                return True, f"using {cmd}"
    return False, "Neither docker nor podman available/running"


def c_gemini_key():
    # Check .env file
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY=") and len(line.split("=", 1)[1].strip()) > 0:
                return True, ""
    if os.environ.get("GEMINI_API_KEY"):
        return True, ""
    return False, (
        "GEMINI_API_KEY not set. Either:\n"
        "    1. Create .env with GEMINI_API_KEY=your-key\n"
        "    2. Or: export GEMINI_API_KEY=your-key"
    )


def c_yaml_configs():
    bare = Path("config/mswea_bare.yaml")
    floop = Path("config/mswea_floop.yaml")
    if not bare.exists():
        return False, f"Missing {bare}. Create it with model config for bare arm."
    if not floop.exists():
        return False, f"Missing {floop}. Create it with model config + behaviors for floop arm."
    return True, ""


def c_wrapper_script():
    script = Path("scripts/run_mswea.py")
    if not script.exists():
        return False, f"Missing {script}."
    try:
        r = subprocess.run(
            ["uv", "run", "python", "-c", "import scripts.run_mswea"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False, f"Import failed: {r.stderr[:500]}"
        return True, ""
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, f"Could not verify import: {e}"


def c_splits():
    p = Path("config/splits.json")
    if not p.exists():
        return False, "config/splits.json missing"
    data = json.loads(p.read_text())
    eval_ids = data.get("eval", [])
    if len(eval_ids) < 1:
        return False, "No eval IDs in splits.json"
    return True, f"{len(eval_ids)} eval tasks"


def main():
    checks = [
        ("uv installed", c_uv),
        ("mini-SWE-agent installed", c_mini_swe_agent),
        ("Container runtime (docker/podman)", c_container_runtime),
        ("GEMINI_API_KEY configured", c_gemini_key),
        ("YAML configs exist", c_yaml_configs),
        ("Wrapper script imports", c_wrapper_script),
        ("Splits file valid", c_splits),
    ]

    passed = sum(check(name, fn) for name, fn in checks)
    total = len(checks)

    if passed == total:
        print(f"\nAll {total} checks passed! mini-SWE-agent integration is ready.")
        print("\nNext steps:")
        print("  1. Smoke test:   uv run python -m scripts.run_mswea smoke")
        print("  2. Run bare:     uv run python -m scripts.run_mswea run --arm bare")
        print("  3. Run floop:    uv run python -m scripts.run_mswea run --arm floop")
        print("  4. Import:       uv run python -m scripts.run_mswea import-results --arm bare")
        print("  5. Evaluate:     uv run python -m scripts.run_mswea evaluate")
        print("  6. Analyze:      uv run python -m analysis.analyze")
        sys.exit(0)
    else:
        print(f"\n{passed}/{total} passed. Fix the first failing check and re-run.")
        sys.exit(1)


if __name__ == "__main__":
    main()
