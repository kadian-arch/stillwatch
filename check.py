"""Run every test suite in the project. Exits non zero if any of them fail."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SUITES = (
    ("simulator", ROOT / "ring-event-simulator", "tests/test_simulator.py"),
    ("clock", ROOT / "stillwatch", "tests/test_clock.py"),
    ("rhythm", ROOT / "stillwatch", "tests/test_rhythm.py"),
    ("monitor", ROOT / "stillwatch", "tests/test_monitor.py"),
    ("notify", ROOT / "stillwatch", "tests/test_notify.py"),
    ("service", ROOT / "stillwatch", "tests/test_service.py"),
    ("ring", ROOT / "stillwatch", "tests/test_ring.py"),
    ("narrate", ROOT / "stillwatch", "tests/test_narrate.py"),
    ("cli", ROOT / "stillwatch", "tests/test_cli.py"),
    ("feed", ROOT, "tests/test_feed.py"),
)

SCRIPTS = (ROOT / "stillwatch" / "stillwatch" / "web" / "app.js",)
PAGE = ROOT / "stillwatch" / "stillwatch" / "web" / "index.html"
STYLE = ROOT / "stillwatch" / "stillwatch" / "web" / "style.css"

# Every state is a word as well as a colour, and the word is small text on a
# card. Four of these were below the readable threshold, the worst of them
# being `alert` in the dark theme, which is the one word in this product that
# has to be legible to somebody who has just woken up.
TEXT_TOKENS = ("--faint", "--muted", "--normal", "--quiet", "--concern",
               "--alert", "--away", "--settled", "--unknown")
CARDS = {"light": "#ffffff", "dark": "#191b21"}
READABLE = 4.5


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


def check_element_ids():
    """Every id the browser script reaches for must exist in the page.

    A missing one is not a syntax error, so node --check passes and the whole
    page renders blank. It has happened twice.
    """
    script = SCRIPTS[0].read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    wanted = sorted(set(re.findall(r'\$\("([^"]+)"\)', script)))
    present = set(re.findall(r'id="([^"]+)"', page))
    missing = [name for name in wanted if name not in present]
    for name in missing:
        print("  FAIL  app.js reaches for #%s, which the page does not have" % name)
    if not missing:
        print("  pass  all %d element ids exist in the page" % len(wanted))
    return 1 if missing else 0


def _linear(value):
    value /= 255.0
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(colour):
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return (0.2126 * _linear(red) + 0.7152 * _linear(green)
            + 0.0722 * _linear(blue))


def _contrast(one, two):
    first, second = _luminance(one), _luminance(two)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def _palettes():
    """The light block, then everything from the dark overrides onwards."""
    css = STYLE.read_text(encoding="utf-8")
    head, _, tail = css.partition("@media (prefers-color-scheme: dark)")
    found = {}
    for theme, block in (("light", head), ("dark", tail)):
        found[theme] = {
            name: value for name, value in
            re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;", block)
        }
    return found


def check_palette():
    """Every state word has to be readable on the card it sits on.

    Colour is checked here rather than looked at, because four of these were
    wrong on a page that had been stared at for weeks, and because the states
    also have to stay apart from each other, which is not something an eye
    judges reliably either.
    """
    palettes = _palettes()
    failed = 0
    for theme, card in CARDS.items():
        palette = palettes.get(theme) or {}
        for token in TEXT_TOKENS:
            value = palette.get(token)
            if value is None:
                print("  FAIL  %s theme has no %s" % (theme, token))
                failed += 1
                continue
            seen = _contrast(value, card)
            if seen < READABLE:
                print("  FAIL  %s %s is %s, %.2f against %s, needs %.1f"
                      % (theme, token, value, seen, card, READABLE))
                failed += 1
    if not failed:
        print("  pass  every state word reads at %.1f or better in both themes"
              % READABLE)
    return 1 if failed else 0


def main():
    results = []
    for name, folder, script in SUITES:
        print("\n=== %s ===" % name)
        finished = subprocess.run([sys.executable, script], cwd=folder)
        results.append((name, finished.returncode))

    print("\n=== browser script ===")
    results.append(("script", check_scripts()))

    print("\n=== page wiring ===")
    results.append(("wiring", check_element_ids()))

    print("\n=== colour ===")
    results.append(("colour", check_palette()))

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
