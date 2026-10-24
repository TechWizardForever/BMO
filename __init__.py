import os
import sys
import traceback

import config
from app import App

app = App(config.SCREEN_WIDTH, config.SCREEN_HEIGHT)

exit_code = 0
try:
    app.on_execute()
except BaseException:
    traceback.print_exc()
    exit_code = 1
finally:
    # BMO's own cleanup is done (on_cleanup). Background threads such as the Piper voice may still be
    # inside onnxruntime; Python's thread teardown then aborts the process (exit -6, live 2026-10-20,
    # window closed while he spoke), and the Supervisor took that for a crash and started him again.
    # So leave without that teardown, keeping the exit code: 0 for a normal close, 1 for an error.
    try:
        from loguru import logger
        logger.complete()
    except Exception:
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
