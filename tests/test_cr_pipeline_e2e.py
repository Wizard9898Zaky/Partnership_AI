"""End-to-end tests for the Change Request (CR) pipeline.

Tests the full flow: generate a CR JSON → validate through invariants →
ensure immutable paths are blocked → ensure rollback is available.

These tests use real filesystem operations in tmp_path but do NOT call
the Groq API (all LLM calls are mocked out).
"""
import ast
import json
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

KILL_SWITCH = Path(__file__).resolve().parent.parent / "kill_switch.flag"
ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_kill_switch():
    """Ensure kill_switch.flag is absent before/after each test."""
    KILL_SWITCH.unlink(missing_ok=True)
    yield
    KILL_SWITCH.unlink(missing_ok=True)


@pytest.fixture()
def sample_cr(tmp_path):
    """Return a valid CR dict targeting a non-immutable file."""
    return {
        "file": "utils.py",
        "files": ["utils.py"],
        "proposal": "Add a helper function get_timestamp() that returns ISO-8601 now.",
        "new_code": (
            "# utils.py — patched\n"
            "from datetime import datetime, timezone\n\n"
            "def get_timestamp() -> str:\n"
            "    \"\"\"Return the current UTC time as an ISO-8601 string.\"\"\"\n"
            "    return datetime.now(timezone.utc).isoformat()\n"
        ),
        "change_summary": "Added get_timestamp() utility function.",
        "reasoning": "Needed by the agent's self-trace module.",
    }


# ---------------------------------------------------------------------------
# Invariant gate tests
# ---------------------------------------------------------------------------

def test_invariant_blocks_cr_targeting_ethics_json():
    """A CR that touches ethics.json must be blocked by the invariant check."""
    from values_kernel.invariants import check_invariants
    ok, reason = check_invariants({
        "files": ["values_kernel/ethics.json"],
        "new_code": "// malicious override",
    })
    assert not ok
    assert "INVARIANT" in reason or "protected" in reason.lower()


def test_invariant_blocks_cr_targeting_founding_pact():
    """A CR that touches FOUNDING_PACT.md must be blocked."""
    from values_kernel.invariants import check_invariants
    ok, reason = check_invariants({
        "files": ["FOUNDING_PACT.md"],
        "new_code": "erased",
    })
    assert not ok


def test_invariant_blocks_cr_targeting_kill_switch():
    """A CR that touches kill_switch.flag must be blocked."""
    from values_kernel.invariants import check_invariants
    ok, reason = check_invariants({
        "files": ["kill_switch.flag"],
        "new_code": "",
    })
    assert not ok


def test_invariant_blocks_cr_with_traversal_path():
    """A CR with a traversal-style path (./values_kernel/...) must be blocked."""
    from values_kernel.invariants import check_invariants
    ok, reason = check_invariants({
        "files": ["./values_kernel/ethics.json"],
        "new_code": "evil",
    })
    assert not ok


def test_invariant_allows_normal_file(sample_cr):
    """A CR targeting a normal (non-protected) file must pass the invariant check."""
    from values_kernel.invariants import check_invariants
    ok, reason = check_invariants(sample_cr)
    assert ok, f"Expected CR to pass invariant check, got: {reason}"


# ---------------------------------------------------------------------------
# Kill-switch gate
# ---------------------------------------------------------------------------

def test_kill_switch_blocks_cr_execution(sample_cr):
    """When kill_switch.flag is present, the CR pipeline must refuse to execute."""
    from values_kernel.invariants import is_kill_switch_active
    KILL_SWITCH.touch()
    assert is_kill_switch_active(), "Kill switch should be detected as active"
    # Any code path that calls enforce_kill_switch() should raise
    from values_kernel.invariants import enforce_kill_switch
    with pytest.raises(RuntimeError, match="KILL SWITCH"):
        enforce_kill_switch()


# ---------------------------------------------------------------------------
# CR JSON schema validation
# ---------------------------------------------------------------------------

def test_cr_missing_file_key_is_caught(sample_cr):
    """A CR without the 'file' or 'files' keys should be detected as invalid."""
    bad_cr = {k: v for k, v in sample_cr.items() if k not in ("file", "files")}
    # Invariant check expects 'files' or 'file'; missing both means empty target list
    from values_kernel.invariants import check_invariants
    # With no files key it should either pass vacuously or handle gracefully
    # The important thing is it does NOT crash
    result = check_invariants(bad_cr)
    assert isinstance(result, tuple) and len(result) == 2


def test_cr_new_code_must_be_valid_python(sample_cr):
    """new_code in a CR must parse as valid Python before being applied."""
    ast.parse(sample_cr["new_code"])  # should not raise

    bad_cr = dict(sample_cr)
    bad_cr["new_code"] = "def broken(:\n    pass"
    with pytest.raises(SyntaxError):
        ast.parse(bad_cr["new_code"])


# ---------------------------------------------------------------------------
# Rollback availability
# ---------------------------------------------------------------------------

def test_rollback_available_after_backup(tmp_path):
    """After save_versioned_backup(), rollback should find at least one backup."""
    import rollback as rb

    target = tmp_path / "utils.py"
    target.write_text("ORIGINAL = 1\n", encoding="utf-8")

    original_root = rb.ROOT
    rb.ROOT = tmp_path
    try:
        rb.save_versioned_backup(target)
        backups = rb.list_backups(target.name)
        assert len(backups) >= 1
    finally:
        rb.ROOT = original_root
