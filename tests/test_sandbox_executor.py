"""
Tests for sandbox_executor.SandboxedExecutor.

These specifically target the bug class found during review: the
import validator previously never consulted its own allowlist and let
`os` (and anything not in a 7-word substring blocklist) straight
through, meaning os.system()/os.popen() shell-out was always available
to "sandboxed" code.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sandbox_executor import SandboxedExecutor


def _executor():
    return SandboxedExecutor(timeout_seconds=5, max_memory_mb=64, max_cpu_seconds=5)


def test_legitimate_code_runs():
    ex = _executor()
    result = ex.execute('import json\nprint(json.dumps({"a": 1}))')
    assert result["success"] is True
    assert '"a": 1' in result["output"]


def test_os_system_is_blocked():
    ex = _executor()
    result = ex.execute('import os\nos.system("echo pwned")')
    assert result["success"] is False


def test_os_popen_is_blocked():
    ex = _executor()
    result = ex.execute('import os\nprint(os.popen("whoami").read())')
    assert result["success"] is False


def test_dunder_class_escape_is_blocked():
    ex = _executor()
    result = ex.execute("print(().__class__.__bases__[0].__subclasses__())")
    assert result["success"] is False


def test_eval_is_blocked():
    ex = _executor()
    result = ex.execute('eval("1+1")')
    assert result["success"] is False


def test_dunder_import_is_blocked():
    ex = _executor()
    result = ex.execute('__import__("os").system("echo pwned")')
    assert result["success"] is False


def test_modules_outside_allowlist_are_blocked():
    ex = _executor()
    for module in ("ctypes", "multiprocessing", "socket", "subprocess", "shutil"):
        result = ex.execute(f"import {module}\nprint({module})")
        assert result["success"] is False, f"{module} should have been blocked"


def test_allowed_modules_are_not_blocked():
    ex = _executor()
    for module in ("json", "re", "math", "random", "datetime", "collections", "itertools"):
        result = ex.execute(f"import {module}\nprint('ok')")
        assert result["success"] is True, f"{module} should be allowed but was blocked"


def test_syntax_error_is_rejected():
    ex = _executor()
    result = ex.execute("def broken(:\n    pass")
    assert result["success"] is False


def test_memory_limit_is_enforced():
    ex = SandboxedExecutor(timeout_seconds=10, max_memory_mb=32, max_cpu_seconds=10)
    # Try to allocate ~500MB against a 32MB ceiling - should fail.
    result = ex.execute('x = "a" * (500 * 1024 * 1024)\nprint(len(x))')
    assert result["success"] is False


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS: {t.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL: {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
