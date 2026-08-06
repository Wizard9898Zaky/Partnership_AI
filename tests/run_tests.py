#!/usr/bin/env python3
"""
Test runner for Partnership_AI.

Preferred usage (if pytest is installed - it's in requirements.txt):
    pytest tests/

This script is a dependency-free fallback that does the same basic
job (discover tests/test_*.py, run every test_* function, report
pass/fail) for environments where pytest isn't available yet - useful
right after a fresh Termux install, before `pip install -r
requirements.txt` has been run.

Also used internally by reviewer.py's pre-accept smoke test gate.
"""
import importlib.util
import sys
import types
import traceback
from pathlib import Path

TESTS_DIR = Path(__file__).parent

# ── Global test fixture: mock the groq_backend module ───────────────────────
# Root cause this works around: conversation_engine/action_registry.py has a
# module-level (i.e. one-time, process-wide) side-effect import of
# conversation_engine.tools to trigger @register_action decorators. Those
# tool modules import groq_backend, which itself does `from groq import Groq`
# - the real `groq` package isn't installed in every environment this suite
# might run in (e.g. CI without network access, or a fresh checkout before
# `pip install -r requirements.txt`). When that import fails, it's caught by
# a try/except inside action_registry.py, but ACTIONS/ACTION_METADATA are
# left permanently empty for the rest of the process - and because Python
# only executes a module's top-level code once (caching it in sys.modules),
# no amount of per-test save/restore logic can fix this after the fact.
# Installing a mock here, before any test file gets a chance to trigger that
# first import, makes test results independent of which file happens to run
# first and whether a real groq install is present.
if "groq_backend" not in sys.modules:
    _mock_groq_backend = types.ModuleType("groq_backend")
    _mock_groq_backend.generate_response = lambda prompt, model=None, **kw: "mock"
    _mock_groq_backend.DEFAULT_MODEL = "mock-model"
    sys.modules["groq_backend"] = _mock_groq_backend


def run_all(verbose: bool = True) -> tuple[int, int, list[str]]:
    """Returns (passed_count, failed_count, failure_messages)."""
    total_passed, total_failed = 0, 0
    failure_messages = []

    for test_file in sorted(TESTS_DIR.glob("test_*.py")):
        module_name = f"_pact_test_{test_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, test_file)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            total_failed += 1
            msg = f"{test_file.name}: FAILED TO LOAD - {e}"
            failure_messages.append(msg)
            if verbose:
                print(f"  FAIL (load error): {test_file.name}")
                traceback.print_exc()
            continue

        test_fns = [
            (name, obj) for name, obj in vars(module).items()
            if name.startswith("test_") and callable(obj)
        ]
        if verbose:
            print(f"\n{test_file.name} ({len(test_fns)} tests)")
        for name, fn in test_fns:
            try:
                fn()
                total_passed += 1
                if verbose:
                    print(f"  PASS: {name}")
            except Exception as e:
                total_failed += 1
                msg = f"{test_file.name}::{name} - {e}"
                failure_messages.append(msg)
                if verbose:
                    print(f"  FAIL: {name}")
                    traceback.print_exc()

    return total_passed, total_failed, failure_messages


if __name__ == "__main__":
    passed, failed, failures = run_all(verbose=True)
    print(f"\n{'='*60}")
    print(f"{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
