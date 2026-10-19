import logging
import os
import sys

from .cli import main

if __name__ == "__main__":
    code = main()
    # Leave without Python's own shutdown: everything is written and flushed here, and a native library (OpenCV's
    # dnn, the CUDA runtime) tearing down its threads at exit can abort the process -- day 12: a laptop run printed
    # its PASS, then Windows ended it with exit code 3 and the app showed a FAIL. MATCH_CUTS_NORMAL_EXIT=1 keeps the
    # normal shutdown.
    if os.environ.get("MATCH_CUTS_NORMAL_EXIT") == "1":
        raise SystemExit(code)
    code = code if isinstance(code, int) else (0 if code is None else 1)
    try:
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(code)
