"""Keep the workflow independent of the launching agent's Python runtime."""

import os
import sys

# Old plugin handlers and terminal commands may omit the adapter's isolation
# flags. Protect the actual executable boundary before importing dependencies.
for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"):
    os.environ.pop(key, None)
os.environ["PYTHONNOUSERSITE"] = "1"
if not (sys.flags.ignore_environment and sys.flags.no_user_site):
    os.execv(sys.executable, [
        sys.executable, "-E", "-s", "-m", "component_datasheet_workflow", *sys.argv[1:],
    ])

from .cli import main

raise SystemExit(main())
