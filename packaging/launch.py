"""Entry script for the packaged app (PyInstaller needs a plain script, not a module)."""

import sys

from engineer.launcher import main

if __name__ == "__main__":
    sys.exit(main())
