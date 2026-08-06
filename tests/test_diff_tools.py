"""
tests/test_diff_tools.py
Tests for watch_file, check_file_diff, and list_watched actions.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from conversation_engine.action_registry import ACTIONS, ActionResult
from conversation_engine.tools import diff_tools


class TestDiffToolRegistration:
    def test_watch_file_registered(self):
        assert "watch_file" in ACTIONS

    def test_check_file_diff_registered(self):
        assert "check_file_diff" in ACTIONS

    def test_list_watched_registered(self):
        assert "list_watched" in ACTIONS


class TestWatchFile:
    def test_watch_whitelisted_file(self):
        """Watch a file that's on the whitelist."""
        with patch.object(diff_tools, "_WATCH_FILE", Path(tempfile.mktemp(suffix=".json"))):
            with patch.object(diff_tools, "_load_watch_state", return_value={}):
                with patch.object(diff_tools, "_save_watch_state"):
                    with patch.object(diff_tools, "_file_sha256", return_value="abc123"):
                        with patch("conversation_engine.tools.diff_tools.safe_resolve_path") as mock_resolve:
                            mock_path = Path(__file__)
                            mock_resolve.return_value = mock_path
                            with patch.object(Path, "stat") as mock_stat:
                                mock_stat.return_value = type("S",(),{"st_size":100})()
                                result = ACTIONS["watch_file"]("test_file.py")
                                assert result.success
                                assert "hash" in result.data

    def test_watch_nonexistent_file(self):
        """Watching a file that doesn't exist should fail."""
        with patch("conversation_engine.tools.diff_tools.safe_resolve_path") as mock_resolve:
            mock_path = mock_resolve.return_value
            mock_path.exists.return_value = False
            result = ACTIONS["watch_file"]("nonexistent.py")
            assert not result.success
            assert "not found" in result.error.lower()


class TestCheckFileDiff:
    def test_not_watched(self):
        """Checking a file that isn't watched should fail."""
        with patch.object(diff_tools, "_load_watch_state", return_value={}):
            with patch("conversation_engine.tools.diff_tools.safe_resolve_path"):
                result = ACTIONS["check_file_diff"]("unwatched_file.py")
                assert not result.success
                assert "not being watched" in result.error.lower()

    def test_no_change_detected(self):
        """Same hash → no change."""
        mock_state = {"test.py": {"hash": "abc123", "size": 100, "content_snapshot": "hello"}}
        with patch.object(diff_tools, "_load_watch_state", return_value=mock_state):
            with patch.object(diff_tools, "_file_sha256", return_value="abc123"):
                with patch("conversation_engine.tools.diff_tools.safe_resolve_path"):
                    result = ACTIONS["check_file_diff"]("test.py")
                    assert result.success
                    assert not result.data["changed"]

    def test_change_detected(self):
        """Different hash → changed=True."""
        mock_state = {"test.py": {"hash": "old_hash", "size": 100, "content_snapshot": "old content\n"}}
        with patch.object(diff_tools, "_load_watch_state", return_value=mock_state):
            with patch.object(diff_tools, "_file_sha256", return_value="new_hash"):
                with patch("conversation_engine.tools.diff_tools.safe_resolve_path") as mock_resolve:
                    mock_path = mock_resolve.return_value
                    mock_path.read_text.return_value = "new content\n"
                    with patch.object(Path, "stat") as mock_stat:
                        mock_stat.return_value = type("S",(),{"st_size":100})()
                        with patch.object(diff_tools, "_save_watch_state"):
                            result = ACTIONS["check_file_diff"]("test.py")
                            assert result.success
                            assert result.data["changed"]
                            assert "diff" in result.data


class TestListWatched:
    def test_empty(self):
        with patch.object(diff_tools, "_load_watch_state", return_value={}):
            result = ACTIONS["list_watched"]()
            assert result.success
            assert result.data["count"] == 0

    def test_shows_files(self):
        mock_state = {"a.py": {"hash": "h1", "size": 100, "watched_at": "2026-01-01T00:00:00Z"}}
        with patch.object(diff_tools, "_load_watch_state", return_value=mock_state):
            result = ACTIONS["list_watched"]()
            assert result.success
            assert result.data["count"] == 1
            assert result.data["files"][0]["file"] == "a.py"
