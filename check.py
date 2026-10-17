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
    ("delivery", ROOT / "stillwatch", "tests/test_delivery.py"),
    ("failure", ROOT / "stillwatch", "tests/test_failure.py"),
    ("narrate", ROOT / "stillwatch", "tests/test_narrate.py"),
    ("cli", ROOT / "stillwatch", "tests/test_cli.py"),
    ("feed", ROOT, "tests/test_feed.py"),
)

SCRIPTS = (ROOT / "stillwatch" / "stillwatch" / "web" / "app.js",
           ROOT / "stillwatch" / "stillwatch" / "web" / "theme.js")
PAGE = ROOT / "stillwatch" / "stillwatch" / "web" / "index.html"
STYLE = ROOT / "stillwatch" / "stillwatch" / "web" / "style.css"


def _rgb(colour):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def _blend(colour, ground, weight):
    """`color-mix(in srgb, colour weight, ground)`, worked out in Python."""
    if colour is None or ground is None:
        return ground
    near, far = _rgb(colour), _rgb(ground)
    return "#%02x%02x%02x" % tuple(
        int(round(near[i] * weight + far[i] * (1 - weight))) for i in range(3))


def _wash(pattern):
    """Read a mix percentage back off the stylesheet.

    Typing it here as well would mean the check kept testing the old wash
    after somebody changed the real one, which is the failure this whole
    file exists to prevent.
    """
    found = re.search(pattern, STYLE.read_text(encoding="utf-8"))
    return float(found.group(1)) / 100.0 if found else 0.0


# Every state is a word as well as a colour, and the word is small text on a
# card. Four of these were below the readable threshold, the worst of them
# being `alert` in the dark theme, which is the one word in this product that
# has to be legible to somebody who has just woken up.
TEXT_TOKENS = ("--faint", "--muted", "--normal", "--quiet", "--concern",
               "--alert", "--away", "--settled", "--unknown")
STATES = tuple(t for t in TEXT_TOKENS if t not in ("--faint", "--muted"))
READABLE = 4.5

MSG_TINT = _wash(r"var\(--u\) ([0-9.]+)%, var\(--surface-2\)")
PILL_TINT = _wash(
    r"background: color-mix\(in srgb, var\(--u\) ([0-9.]+)%, transparent\)")


def check_inline_script():
    """The page is served under `default-src \'self\'`, which refuses inline
    script without running it and without saying so anywhere the author looks.

    A theme bootstrap was written straight into the document, the browser
    declined it, and the page went on looking exactly as it had. Every suite
    stayed green. So the rule is checked here instead of being remembered.
    """
    page = PAGE.read_text(encoding="utf-8")
    loose = [tag for tag in re.findall(r"<script\b[^>]*>", page)
             if "src=" not in tag]
    if loose:
        print("  FAIL  the page carries script the policy will refuse: %s"
              % ", ".join(loose))
        return 1

    # And the files it does ask for have to be there to ask for.
    missing = [name for name in re.findall(r'<script src="/static/([^"]+)"', page)
               if not (PAGE.parent / name).exists()]
    if missing:
        print("  FAIL  the page asks for %s, which is not in web/"
              % ", ".join(missing))
        return 1

    print("  pass  no inline script, and every script it asks for exists")
    return 0


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
    head, _, tail = css.partition(''':root[data-theme="dark"]''')
    found = {}
    for theme, block in (("light", head), ("dark", tail)):
        found[theme] = {
            name: value for name, value in
            re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block)
        }
    return found


def _grounds(palette, token, value):
    """Every background this token can end up on.

    It is not only the card. A state word also sits on the inset panels, on a
    message tinted with its own colour, and inside the pill, which is that
    colour again over the tint. Checking against white alone passed five
    light values that were short of the line everywhere else on the page.
    """
    card = palette.get("--surface")
    panel = palette.get("--surface-2") or card
    if token in ("--faint", "--muted"):
        # Quiet text is never tinted with itself, but it does sit on messages
        # tinted with somebody else's state.
        return (card, panel) + tuple(
            _blend(palette[s], panel, MSG_TINT)
            for s in STATES if s in palette)
    tinted = _blend(value, panel, MSG_TINT)
    return (card, panel, tinted, _blend(value, tinted, PILL_TINT))


def check_palette():
    """Every state word has to be readable wherever it is put.

    Colour is checked here rather than looked at, because four of these were
    wrong on a page that had been stared at for weeks, and because the states
    also have to stay apart from each other, which is not something an eye
    judges reliably either.
    """
    palettes = _palettes()
    failed = 0
    for theme in ("light", "dark"):
        palette = palettes.get(theme) or {}
        if palette.get("--surface") is None:
            print("  FAIL  %s theme has no --surface to sit a card on" % theme)
            failed += 1
            continue
        for token in TEXT_TOKENS:
            value = palette.get(token)
            if value is None:
                print("  FAIL  %s theme has no %s" % (theme, token))
                failed += 1
                continue
            for ground in _grounds(palette, token, value):
                if ground is None:
                    continue
                seen = _contrast(value, ground)
                if seen < READABLE:
                    print("  FAIL  %s %s is %s, %.2f against %s, needs %.1f"
                          % (theme, token, value, seen, ground, READABLE))
                    failed += 1
                    break
    # The browser paints its own bar on a phone from this, so it has to be
    # the colour the page opens on rather than one that was right once.
    light = (palettes.get("light") or {}).get("--bg")
    tint = re.search(r'<meta name="theme-color" content="(#[0-9a-fA-F]{6})',
                     PAGE.read_text(encoding="utf-8"))
    if tint is None:
        print("  FAIL  the page declares no theme-color")
        failed += 1
    elif light is None or tint.group(1).lower() != light.lower():
        print("  FAIL  theme-color is %s but the page opens on %s"
              % (tint.group(1), light))
        failed += 1

    if not failed:
        print("  pass  every state word reads at %.1f or better in both themes"
              % READABLE)
        print("  pass  the browser bar matches the colour the page opens on")
    return 1 if failed else 0


def replay_days():
    """Generate the recorded days if they are not there yet.

    They are built from a seed rather than committed, so a fresh clone has
    none and the suites that replay them have nothing to read. Running the
    documented command on a clean checkout should not report a failure that
    only means a generated file is missing.
    """
    folder = ROOT / "stillwatch" / "data"
    if folder.is_dir() and any(folder.glob("*.json")):
        return 0
    print("\n=== recorded days ===")
    print("  generating them, because %s is empty" % folder)
    finished = subprocess.run([sys.executable, str(ROOT / "demo_data.py")],
                              cwd=ROOT, capture_output=True, text=True)
    if finished.returncode != 0:
        print(finished.stdout[-2000:])
        print(finished.stderr[-2000:])
        return 1
    print("  built the days the replay suites read")
    return 0



def check_entry_points():
    """The two ways of starting the service must build the same thing.

    Written after a real miss. A reporter was added to the command line
    `serve`, every test passed, and production reported nothing for a day
    because the Procfile runs `gunicorn wsgi:app` and that file builds its own
    watcher. A second look found the reverse as well: the command line was not
    giving the watcher a narrator, so running locally exercised different
    wording from the server.

    Neither was a logic error and no test could have caught either, because
    each path was correct on its own. Only comparing them finds it.
    """
    import ast

    def keywords(path, name):
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == name):
                return {k.arg for k in node.keywords if k.arg}
        return None

    production = ROOT / "wsgi.py"
    command_line = ROOT / "stillwatch" / "stillwatch" / "cli.py"

    failed = 0
    for name in ("create_app", "LiveWatcher"):
        here = keywords(production, name)
        there = keywords(command_line, name)
        if here is None or there is None:
            print("  FAIL  %s is not built in both places" % name)
            failed += 1
            continue
        if here != there:
            missing = sorted(here - there) or sorted(there - here)
            where = "the serve command" if here - there else "wsgi.py"
            print("  FAIL  %s differs: %s does not pass %s"
                  % (name, where, ", ".join(missing)))
            failed += 1
        else:
            print("  pass  %s is built the same way in both" % name)

    # Every setting the code reads should be written down somewhere a person
    # deploying this would look.
    import re

    read = set()
    for path in ROOT.rglob("*.py"):
        if "test" in path.name or ".git" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        read.update(re.findall(r"environ\.get\(\"(STILLWATCH_[A-Z_]+)\"", text))

    written = ""
    for doc in (ROOT / "wsgi.py", ROOT / "docs" / "DEPLOY.md", ROOT / "README.md",
                ROOT / "docs" / "SECURITY.md"):
        if doc.exists():
            written += doc.read_text(encoding="utf-8", errors="replace")

    undocumented = sorted(v for v in read if v not in written)
    if undocumented:
        print("  FAIL  settings read but documented nowhere: %s"
              % ", ".join(undocumented))
        failed += 1
    else:
        print("  pass  all %d settings the code reads are documented" % len(read))

    return 1 if failed else 0

def main():
    if replay_days():
        print("\ncould not generate the recorded days")
        return 1

    results = []
    for name, folder, script in SUITES:
        print("\n=== %s ===" % name)
        finished = subprocess.run([sys.executable, script], cwd=folder)
        results.append((name, finished.returncode))

    print("\n=== browser script ===")
    results.append(("script", check_scripts()))
    results.append(("inline", check_inline_script()))

    print("\n=== page wiring ===")
    results.append(("wiring", check_element_ids()))

    print("\n=== entry points ===")
    results.append(("entries", check_entry_points()))

    print("\n=== colour ===")
    results.append(("colour", check_palette()))

    print("\n%s" % ("-" * 40))
    failed = [name for name, code in results if code != 0]
    for name, code in results:
        print("  %-12s %s" % (name, "ok" if code == 0 else "FAILED"))

    # Twelve suites are test files; the last three are static checks on the
    # page and its palette. Counting all fifteen as suites overstates it.
    checks = len(results) - len(SUITES)
    if failed:
        print("\n%d of %d failed: %s" % (len(failed), len(results), ", ".join(failed)))
        return 1
    print("\nall %d test suites and %d static checks passed"
          % (len(SUITES), checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
