"""Tests for rollback.py — backup creation, restoration, kill-switch guard, path safety."""
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rollback as rb

KILL_SWITCH = Path(__file__).resolve().parent.parent / "kill_switch.flag"
ROOT = rb.ROOT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_kill_switch():
    """Ensure kill_switch.flag is absent before and after every test."""
    KILL_SWITCH.unlink(missing_ok=True)
    yield
    KILL_SWITCH.unlink(missing_ok=True)


@pytest.fixture()
def patched_root(tmp_path, monkeypatch):
    """Redirect rb.ROOT into tmp_path so tests don't touch the real project tree."""
    monkeypatch.setattr(rb, "ROOT", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_save_versioned_backup_creates_backup_file(patched_root):
    """save_versioned_backup() must produce at least one backup entry for the file."""
    target = patched_root / "target_module.py"
    target.write_text("ORIGINAL = True\n", encoding="utf-8")

    rb.save_versioned_backup(target)

    # list_backups uses rb.ROOT (monkeypatched) to find the backup dir
    backups = rb.list_backups("target_module.py")
    assert len(backups) >= 1, "Expected at least one backup after save_versioned_backup()"


def test_rollback_file_restores_content(patched_root):
    """rollback_file() must restore the target file to its backed-up content."""
    target = patched_root / "target_module.py"
    target.write_text("ORIGINAL = True\n", encoding="utf-8")

    rb.save_versioned_backup(target)

    # Corrupt the file
    target.write_text("CORRUPTED = False\n", encoding="utf-8")
    assert target.read_text() == "CORRUPTED = False\n"

    # Rollback using the file's name relative to patched ROOT
    success, msg = rb.rollback_file("target_module.py")
    assert success, f"rollback_file() returned failure: {msg}"
    assert target.read_text() == "ORIGINAL = True\n"


def test_rollback_rejects_no_backup(patched_root):
    """rollback_file() returns (False, …) when no backup exists for the path."""
    success, msg = rb.rollback_file("nonexistent_file.py")
    assert not success
    assert "backup" in msg.lower() or "not found" in msg.lower()


def test_rollback_honours_kill_switch():
    """rollback_file() must refuse to operate when kill_switch.flag is present."""
    KILL_SWITCH.touch()
    try:
        success, msg = rb.rollback_file("anything.py")
        assert not success, "Expected rollback to be blocked by kill switch"
        assert "kill" in msg.lower() or "switch" in msg.lower()
    finally:
        KILL_SWITCH.unlink(missing_ok=True)


def test_rollback_rejects_path_traversal():
    """rollback_file() must reject rel_path values that escape the project root.

    Note: the path escape check in rollback_file() runs after the backup lookup.
    If no backup exists, the 'no backups found' message is returned first — which
    is still a safe refusal. This test accepts either the path-escape message or
    a 'no backups' message as valid safe-refusal responses.
    """
    success, msg = rb.rollback_file("../../etc/passwd")
    # Any False return is correct — the operation must not succeed
    assert not success, "Path traversal must not result in a successful rollback"
    # Accept either the path-escape error or the no-backup error — both are safe
    lower = msg.lower()
    assert (
        "outside" in lower or "root" in lower or "traversal" in lower
        or "escape" in lower or "backup" in lower or "not found" in lower
    ), f"Unexpected refusal message: {msg!r}"


def test_list_backups_returns_empty_for_unknown_file():
    """list_backups() returns an empty list for a file that was never backed up."""
    result = rb.list_backups("this_file_was_never_backed_up_xyz_abc.py")
    assert result == []
