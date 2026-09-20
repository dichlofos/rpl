import sys

from .probe import main as probe_main
from .webapp import main as web_main

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        raise SystemExit(web_main(sys.argv[2:]))
    raise SystemExit(probe_main())
