import sys

from engineer.cli import main

if __name__ == "__main__":  # worker processes may import this module; only run the CLI here
    sys.exit(main())
