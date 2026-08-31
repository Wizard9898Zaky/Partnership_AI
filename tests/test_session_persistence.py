"""Tests for the session persistence layer in new_main_chat.py.

Session data lives only in the encrypted log (log-{user_hash}.enc): the
decrypted contents are handed to _set_last_session_cache() at startup and
load_last_session() reads them back. save_session_dump() is a no-op stub
kept for backward compatibility — it must never write plaintext to disk.
"""
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_dump_dir(tmp_path, monkeypatch):
    """Redirect the (deprecated) dump directory into tmp_path for every test."""
    import new_main_chat as nmc
    monkeypatch.setattr(nmc, "_session_dump_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def nmc(monkeypatch):
    import new_main_chat as m
    monkeypatch.setattr(m, "_last_session_cache", "", raising=False)
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

def test_save_writes_no_plaintext_file(nmc, tmp_path):
    """save_session_dump() must not write any plaintext dump to disk."""
    nmc.save_session_dump(FAKE_LOG, FAKE_HASH)
    assert list(tmp_path.iterdir()) == []


def test_save_no_op_on_empty_log(nmc, tmp_path):
    """save_session_dump() must not create any file when the log is empty."""
    nmc.save_session_dump([], FAKE_HASH)
    assert list(tmp_path.iterdir()) == []


def test_save_does_not_populate_the_session_cache(nmc):
    """save_session_dump() is inert — it must not affect load_last_session()."""
    nmc.save_session_dump(FAKE_LOG, FAKE_HASH)
    assert nmc.load_last_session(FAKE_HASH) == ""


# ---------------------------------------------------------------------------
# load_last_session
# ---------------------------------------------------------------------------

def test_load_returns_empty_when_cache_is_unset(nmc):
    """load_last_session() returns '' when no encrypted log was decrypted."""
    assert nmc.load_last_session("zzzzzzzzzzzzzzzz") == ""


def test_load_restores_cached_content(nmc):
    """load_last_session() returns the turns cached from the encrypted log."""
    nmc._set_last_session_cache("".join(FAKE_LOG))
    restored = nmc.load_last_session(FAKE_HASH)
    assert "Hello" in restored
    assert "Hi Zak" in restored


def test_load_max_turns_limits_output(nmc):
    """load_last_session(max_turns=1) returns only the last turn."""
    nmc._set_last_session_cache("".join(FAKE_LOG))
    restored = nmc.load_last_session(FAKE_HASH, max_turns=1)
    assert "Great" in restored
    assert "Hello" not in restored


def test_load_reflects_the_most_recent_cache_write(nmc):
    """A later _set_last_session_cache() call replaces the earlier one."""
    nmc._set_last_session_cache("[ts]\nUSER: old\nAI: old reply\n\n")
    nmc._set_last_session_cache("[ts]\nUSER: new\nAI: new reply\n\n")
    restored = nmc.load_last_session(FAKE_HASH)
    assert "new reply" in restored
    assert "old reply" not in restored


def test_load_ignores_blank_turns(nmc):
    """Blank separators in the decrypted log are dropped, not counted as turns."""
    nmc._set_last_session_cache("USER: a\nAI: b\n\n\n\n\n\nUSER: c\nAI: d\n\n")
    restored = nmc.load_last_session(FAKE_HASH, max_turns=1)
    assert "USER: c" in restored
    assert "USER: a" not in restored
