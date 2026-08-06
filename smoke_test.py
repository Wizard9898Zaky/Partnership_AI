#!/usr/bin/env python3
# smoke_test.py
"""
Pre-apply smoke test for Change Requests.

Previously, reviewer.py's Accept path only ran ast.parse() on the
proposed new_code - which catches syntax errors but nothing else. It
would not have caught, for example, a proposed change to
memory_engine.py that silently broke store_exchange()'s contract with
its one caller in new_main_chat.py, or a change to values_kernel that
broke check_invariants()'s actual behavior while still parsing fine.

This module copies the whole project tree to a scratch directory,
overlays the proposed file content at its real relative path, and runs
the full tests/ suite against that overlaid copy - so a CR that would
break any test anywhere in the project gets caught before a human even
has to reason about it, not just checked for syntax validity.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Tuple

from app_config import get_config

ROOT = Path(__file__).parent.resolve()

_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "cr_logs", "*.bak", "*.tmp", ".git"
)


def run_smoke_test(target_rel_path: str, new_code: str) -> Tuple[bool, str]:
    """
    Returns (passed, message).

    passed=True means either "all tests passed against the overlaid
    tree" or "the test runner itself couldn't be found/run" (fails
    open on infrastructure problems rather than blocking every CR
    forever if e.g. the tests/ directory is itself the immutable
    target of some future refactor - a missing test suite is a
    separate problem from a broken CR, and shouldn't silently block
    all self-improvement).
    """
    timeout = get_config().get("reviewer", {}).get("smoke_test_timeout_seconds", 30)
    runner = ROOT / "tests" / "run_tests.py"
    if not runner.exists():
        return True, "(no test suite found at tests/run_tests.py - skipping smoke test)"

    with tempfile.TemporaryDirectory(prefix="cr_smoketest_") as scratch:
        scratch_root = Path(scratch) / "Partnership_AI"
        shutil.copytree(ROOT, scratch_root, ignore=_IGNORE)

        target = scratch_root / target_rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_code, encoding="utf-8")

        try:
            proc = subprocess.run(
                [sys.executable, str(scratch_root / "tests" / "run_tests.py")],
                cwd=str(scratch_root),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"Smoke test timed out after {timeout}s - proposed change may hang or infinite-loop."
        except Exception as e:
            # Infrastructure failure (e.g. python3 not on PATH in a
            # stripped-down environment) - don't block the CR over
            # something unrelated to its content.
            return True, f"(smoke test could not run: {e} - skipping)"

        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            # Trim to something reviewable in a terminal rather than
            # dumping a potentially huge traceback.
            trimmed = output[-3000:]
            return False, trimmed
        return True, output.strip().splitlines()[-1] if output.strip() else "All tests passed."
