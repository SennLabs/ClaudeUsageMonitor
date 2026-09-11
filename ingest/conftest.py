"""
Test configuration, and the guard that keeps the suite off the real database.

`app.db` resolves DB_PATH **once at import time**, so the environment has to be
set before anything imports it. conftest.py is the only file pytest is
guaranteed to load before collecting test modules, which is why this lives here
rather than at the top of the test file: without it, the suite deleted
`ingest/usage.db` — the same file local development writes to — on every run.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_TMP_DIR = tempfile.mkdtemp(prefix="claude-usage-tests-")
os.environ["DB_PATH"] = str(Path(_TMP_DIR) / "test_usage.db")
atexit.register(shutil.rmtree, _TMP_DIR, True)

# Most tests exercise the unauthenticated path, which the app refuses to start
# in unless the operator opts in explicitly.
os.environ.setdefault("INGEST_ALLOW_ANONYMOUS", "1")

# Keep the suite's own output readable: the app logs at INFO by default, and
# several tests deliberately provoke warnings.
os.environ.setdefault("LOG_LEVEL", "WARNING")

SCRATCH_DIR = Path(_TMP_DIR)
