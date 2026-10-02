"""Lets `python -m stillwatch` work from the repository root as well.

The package lives at `stillwatch/stillwatch/`, so the command only resolves
from inside `stillwatch/`. In production the working directory is the
repository root, where the name `stillwatch` finds this folder instead, and
the command fails with a message about a package that cannot be executed.

That is a confusing answer to a correct command, and the place somebody runs
it from is the last thing they think to check. So the outer folder answers to
the same command and hands over to the real one.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# This folder is already bound to the name, as the namespace package Python
# found when it looked for something to run. Let go of it, so the import below
# reaches the real package one level down rather than back here.
sys.modules.pop("stillwatch", None)

from stillwatch.cli import main

if __name__ == "__main__":
    sys.exit(main())
