"""Generate every replay scenario for the dashboard, with an index describing them.

The work itself lives in stillwatch.demo, because the deployed service builds
these for itself on a container that starts with an empty disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "stillwatch"))

from stillwatch.demo import build

DATA = ROOT / "stillwatch" / "data"


def main():
    written = build(
        DATA,
        repo_root=ROOT,
        on_each=lambda key, count, day: print(
            "  %-18s %5d events, watch %s" % (key, count, day)),
    )
    print("\nwrote %d scenarios to %s" % (len(written), DATA))
    return 0


if __name__ == "__main__":
    sys.exit(main())
