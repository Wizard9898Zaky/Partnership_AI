"""Tests for the session persistence layer in new_main_chat.py.

Covers save_session_dump(), load_last_session(), and _session_dump_dir().
All tests are hermetic — they redirect the dump directory into tmp_path
so no real disk state is modified.
"""
import json
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_dump_dir(tmp_path, monkeypatch):
    """Redirect all session dumps into tmp_path for every test."""
    import new_main_chat as nmc
    monkeypatch.setattr(nmc, "_session_dump_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def nmc():
    import new_main_chat as m
    return m


FAKE_HASH = "abc123deadbeef99"
FAKE_LOG = [
    "[2026-07-24T08:00:00]\nUSER: Hello\nAI: Hi Zak!\n\n",
    "[2026-07-24T08:01:00]\nUSER: What can you do?\nAI: I can help with code.\n\n",
    "[2026-07-24T08:02:00]\nUSER: Great\nAI: Thanks!\n\n",
]


# ---------------------------------------------------------------------------
# save_session_dump
# ---------------------------------------------------------------------------

def test_save_creates_one_dump_file(nmc, tmp_path):
    """save_session_dump() creates exactly one file in the dump directory."""
    nmc.save_session_dump(FAKE_LOG, FAKE_HASH)
    dumps = list(tmp_path.glob(f"session-{FAKE_HASH[:8]}-*.txt"))
    assert len(dumps) == 1


def test_save_dump_contains_all_turns(nmc, tmp_path):
    """The saved dump file contains the full content of every turn."""
    nmc.save_session_dump(FAKE_LOG, FAKE_HASH)
    dump_file = list(tmp_path.glob(f"session-{FAKE_HASH[:8]}-*.txt"))[0]
    content = dump_file.read_text(encoding="utf-8")
    assert "Hello" in content
    assert "Hi Zak" in content
    assert "What can you do" in content


def test_save_no_op_on_empty_log(nmc, tmp_path):
    """save_session_dump() must not create any file when the log is empty."""
    nmc.save_session_dump([], FAKE_HASH)
    dumps = list(tmp_path.glob(f"session-{FAKE_HASH[:8]}-*.txt"))
    assert len(dumps) == 0


def test_save_respects_dump_on_exit_false(nmc, tmp_path, monkeypatch):
    """save_session_dump() must not create any file when dump_on_exit is False."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["session"]["dump_on_exit"] = False

    # Patch open so save reads the modified config
    original_read = Path.read_text

    def patched_read(self, **kwargs):
        if self.name == "config.json":
            return json.dumps(cfg)
        return original_read(self, **kwargs)

    monkeypatch.setattr(Path, "read_text", patched_read)
    nmc.save_session_dump(FAKE_LOG, FAKE_HASH)
    dumps = list(tmp_path.glob(f"session-{FAKE_HASH[:8]}-*.txt"))
    assert len(dumps) == 0


def test_multiple_saves_create_multiple_files(nmc, tmp_path):
    """Each save_session_dump() call creates a new timestamped file."""
    import time
    nmc.save_session_dump(FAKE_LOG, FAKE_HASH)
    time.sleep(1.1)  # ensure different timestamp in filename
    nmc.save_session_dump(FAKE_LOG, FAKE_HASH)
    dumps = list(tmp_path.glob(f"session-{FAKE_HASH[:8]}-*.txt"))
    assert len(dumps) == 2


# ---------------------------------------------------------------------------
# load_last_session
# ---------------------------------------------------------------------------

def test_load_returns_empty_for_unknown_user(nmc):
    """load_last_session() returns '' when no dump exists for the user hash."""
    result = nmc.load_last_session("zzzzzzzzzzzzzzzz")
    assert result == ""


def test_load_restores_content(nmc, tmp_path):
    """load_last_session() returns the turn content written by save_session_dump()."""
    nmc.save_session_dump(FAKE_LOG, FAKE_HASH)
    restored = nmc.load_last_session(FAKE_HASH)
    assert "Hello" in restored
    assert "Hi Zak" in restored


def test_load_max_turns_limits_output(nmc, tmp_path):
    """load_last_session(max_turns=1) returns only the last turn."""
    nmc.save_session_dump(FAKE_LOG, FAKE_HASH)
    restored = nmc.load_last_session(FAKE_HASH, max_turns=1)
    # Should contain the last turn but not the first
    assert "Great" in restored
    assert "Hello" not in restored


def test_load_picks_most_recent_dump(nmc, tmp_path):
    """load_last_session() loads the most recent dump when several exist."""
    import time
    old_log = ["[ts]\nUSER: old\nAI: old reply\n\n"]
    new_log = ["[ts]\nUSER: new\nAI: new reply\n\n"]
    nmc.save_session_dump(old_log, FAKE_HASH)
    time.sleep(1.1)
    nmc.save_session_dump(new_log, FAKE_HASH)
    restored = nmc.load_last_session(FAKE_HASH)
    assert "new reply" in restored
    assert "old reply" not in restored


def test_load_handles_corrupt_dump_gracefully(nmc, tmp_path):
    """load_last_session() returns '' if the dump file is unreadable/corrupt."""
    # Write a file with invalid UTF-8
    bad = tmp_path / f"session-{FAKE_HASH[:8]}-20260101T000000Z.txt"
    bad.write_bytes(b"\xff\xfe broken utf-8 \x00\x00")
    # Should not raise — just return empty or partial
    result = nmc.load_last_session(FAKE_HASH)
    assert isinstance(result, str)  # graceful — no crash
