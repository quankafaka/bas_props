"""
Central place for every file/folder path in the project.

All paths are built from this file's own location, never from a hardcoded
"/Users/..." path or the current working directory. So they work the same on
any Mac, Windows, or Linux machine, from the terminal or an IDE, wherever the
project folder is cloned.

Expected layout:
    <project root>/
    ├── .env
    └── src/
        ├── general_utility/create_path.py   <- this file
        ├── odds-api/
        │   ├── pull_snapshot.py
        │   └── data/
        └── dashboard/
            ├── server.py
            ├── static/
            └── data/trader_state.json

Usage from any script in src/<folder>/:
    from general_utility.create_path import ODDS_API_DATA_DIR, ENV_FILE

Check paths on a new machine:
    python src/general_utility/create_path.py
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------

GENERAL_UTILITY_DIR = Path(__file__).resolve().parent   # src/general_utility
SRC_DIR = GENERAL_UTILITY_DIR.parent                    # src
PROJECT_ROOT = SRC_DIR.parent                           # bas_props (or whatever it's named)

if SRC_DIR.name != "src":
    raise RuntimeError(
        f"create_path.py expects to live in <project>/src/general_utility/, "
        f"but found it in {GENERAL_UTILITY_DIR}. Update the roots in create_path.py if you moved it."
    )

# ---------------------------------------------------------------------------
# Project-level files
# ---------------------------------------------------------------------------

ENV_FILE = PROJECT_ROOT / ".env"

# ---------------------------------------------------------------------------
# odds-api
# ---------------------------------------------------------------------------

ODDS_API_DIR = SRC_DIR / "odds-api"
ODDS_API_DATA_DIR = ODDS_API_DIR / "data"
ODDS_API_RAW_DIR = ODDS_API_DATA_DIR / "raw"

# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------

DASHBOARD_DIR = SRC_DIR / "dashboard"
DASHBOARD_STATIC_DIR = DASHBOARD_DIR / "static"
DASHBOARD_DATA_DIR = DASHBOARD_DIR / "data"
DASHBOARD_STATE_FILE = DASHBOARD_DATA_DIR / "trader_state.json"

# Add new modules' paths below in the same style.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def project_path(*parts, create=False):
    """Build a path under the project root, e.g. project_path("src", "odds-api", "data").
    create=True makes the folder (and parents) if it doesn't exist."""
    path = PROJECT_ROOT.joinpath(*parts)
    if create:
        ensure_dir(path)
    return path


def ensure_dir(path):
    """Create a folder (and its parents) if missing. Returns the path."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def rel(path):
    """Path relative to the project root, for logs and metadata.
    Avoids exposing machine-specific paths like /Users/<name>/Desktop/..."""
    path = Path(path).resolve()
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    for name, value in [
        ("PROJECT_ROOT", PROJECT_ROOT),
        ("SRC_DIR", SRC_DIR),
        ("ENV_FILE", ENV_FILE),
        ("ODDS_API_DIR", ODDS_API_DIR),
        ("ODDS_API_DATA_DIR", ODDS_API_DATA_DIR),
        ("ODDS_API_RAW_DIR", ODDS_API_RAW_DIR),
        ("DASHBOARD_DIR", DASHBOARD_DIR),
        ("DASHBOARD_STATIC_DIR", DASHBOARD_STATIC_DIR),
        ("DASHBOARD_STATE_FILE", DASHBOARD_STATE_FILE),
    ]:
        status = "exists" if value.exists() else "missing"
        print(f"{name:<21} {value}  [{status}]")
