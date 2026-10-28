"""python -m engineer.report [--daily] [--no-open]"""

import sys

from engineer.report.daily import main

if __name__ == "__main__":
    sys.exit(main())
