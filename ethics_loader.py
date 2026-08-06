#!/usr/bin/env python3
# ethics_loader.py
"""
Loads the immutable ethics.json from the values_kernel directory.
Provides a clean interface for other modules to access core principles.

FIX (v1.1):
  - Removed attempt to rebind the module-level ETHICS_PATH constant inside
    load_core_values(). Python treats any variable assigned inside a function
    as local for the *entire* function, so the previous pattern:

        if not ETHICS_PATH.exists():
            ETHICS_PATH = alt_path          # ← makes ETHICS_PATH "local"
                                             #   → UnboundLocalError on the
                                             #   .exists() call above it

    was guaranteed to crash whenever the primary path was missing.

  - Replaced with a local `path` variable that walks the fallback chain
    without touching the module-level constant.
  - ETHICS_PATH is now truly read-only after module load.
"""

import json
from pathlib import Path

# Resolve path relative to this file, assuming values_kernel is a sibling directory.
BASE_DIR    = Path(__file__).resolve().parent
ETHICS_PATH = BASE_DIR / "values_kernel" / "ethics.json"


def load_core_values() -> dict:
    """
    Load the full ethics.json file.
    Returns the raw JSON dict containing 'core_principles'.

    Search order:
      1. <module_dir>/values_kernel/ethics.json  (canonical install location)
      2. values_kernel/ethics.json               (CWD fallback for unit tests)

    Raises FileNotFoundError if neither path exists.
    Raises RuntimeError if the file is present but cannot be parsed.
    """
    # Use a local variable — never re-assign the module-level constant.
    path = ETHICS_PATH
    if not path.exists():
        alt_path = Path("values_kernel/ethics.json")
        if alt_path.exists():
            path = alt_path
        else:
            raise FileNotFoundError(
                f"ethics.json not found at {ETHICS_PATH} "
                f"or {alt_path.resolve()}"
            )

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in ethics.json: {e}") from e
    except OSError as e:
        raise RuntimeError(f"Failed to read ethics.json: {e}") from e


def get_principles() -> list:
    """Retrieve the list of core ethical principles from ethics.json.

    Returns:
        list: A list of core ethical principle dicts or strings.

    Raises:
        RuntimeError: If ethics.json is missing the mandatory
            'core_principles' key or if the file cannot be read.
    """
    data = load_core_values()
    principles = data.get("core_principles")
    if principles is None:
        raise RuntimeError(
            "ethics.json is missing the mandatory 'core_principles' key. "
            "The system cannot operate without active ethical guardrails."
        )
    return principles
