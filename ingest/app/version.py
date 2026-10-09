"""
The release version, read from the repo-root VERSION file.

That file is the single source of truth for both services. The Docker build
copies it into the image (see the additional_contexts in docker-compose.yml);
in a local checkout it is two directories up from this package.
"""

from pathlib import Path

_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "VERSION",  # /app/VERSION in the image
    Path(__file__).resolve().parent.parent.parent / "VERSION",  # repo root
)


def _read() -> str:
    for path in _CANDIDATES:
        try:
            return path.read_text().strip() or "unknown"
        except OSError:
            continue
    return "unknown"


__version__ = _read()
