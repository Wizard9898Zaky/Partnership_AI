"""
Project-wide syntax regression test.

Every .py file in the project must at least parse as valid Python.
This is the cheapest possible test to write and would have caught,
by itself, the adaptive_agent.py syntax error (four quote characters
colliding at the end of a triple-quoted string) that shipped in an
earlier version of this project and meant the module couldn't even
be imported.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Directories we don't walk into: virtualenvs, caches, and (in a real
# checkout) anything vendored.
_SKIP_DIRS = {"__pycache__", ".git", "cr_logs", "venv", ".venv"}


def _all_python_files():
    for path in ROOT.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def test_all_python_files_parse():
    failures = []
    for path in _all_python_files():
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            failures.append(f"{path.relative_to(ROOT)}: {e}")
    assert not failures, "Syntax errors found:\n" + "\n".join(failures)


def test_at_least_expected_file_count():
    # Sanity check that the walk itself is actually finding files -
    # guards against a future refactor silently making _all_python_files
    # yield nothing (which would make test_all_python_files_parse
    # vacuously pass and give false confidence).
    count = sum(1 for _ in _all_python_files())
    assert count >= 20, f"Expected at least 20 .py files, found {count} - is the test walking the right directory?"


if __name__ == "__main__":
    test_all_python_files_parse()
    test_at_least_expected_file_count()
    print("OK: test_project_syntax.py")
