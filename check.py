"""Run every test suite in the project. Exits non zero if any of them fail."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SUITES = (
    ("simulator", ROOT / "ring-event-simulator", "tests/test_simulator.py"),
    ("rhythm", ROOT / "stillwatch", "tests/test_rhythm.py"),
    ("monitor", ROOT / "stillwatch", "tests/test_monitor.py"),
    ("notify", ROOT / "stillwatch", "tests/test_notify.py"),
    ("service", ROOT / "stillwatch", "tests/test_service.py"),
    ("ring", ROOT / "stillwatch", "tests/test_ring.py"),
    ("narrate", ROOT / "stillwatch", "tests/test_narrate.py"),
)

SCRIPTS = (ROOT / "stillwatch" / "stillwatch" / "web" / "app.js",)


def check_scripts():
    """The browser code has no test suite of its own, so at least prove it parses."""
    node = shutil.which("node")
    if node is None:
        print("node not found, skipping the browser script check")
        return 0
    failed = 0
    for script in SCRIPTS:
        finished = subprocess.run([node, "--check", str(script)])
        print("  %s  %s" % ("pass" if finished.returncode == 0 else "FAIL", script.name))
        failed += finished.returncode != 0
    return 1 if failed else 0


def main():
    results = []
    for name, folder, script in SUITES:
        print("\n=== %s ===" % name)
        finished = subprocess.run([sys.executable, script], cwd=folder)
        results.append((name, finished.returncode))

    print("\n=== browser script ===")
    results.append(("script", check_scripts()))

    print("\n%s" % ("-" * 40))
    failed = [name for name, code in results if code != 0]
    for name, code in results:
        print("  %-12s %s" % (name, "ok" if code == 0 else "FAILED"))

    if failed:
        print("\n%d of %d suites failed" % (len(failed), len(results)))
        return 1
    print("\nall %d suites passed" % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
