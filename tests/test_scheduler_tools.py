"""
tests/test_scheduler_tools.py
Tests for schedule_task, list_scheduled, and cancel_task actions.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from conversation_engine.action_registry import ACTIONS, ActionResult
from conversation_engine.tools import scheduler_tools


class TestSchedulerRegistration:
    def test_schedule_task_registered(self):
        assert "schedule_task" in ACTIONS

    def test_list_scheduled_registered(self):
        assert "list_scheduled" in ACTIONS

    def test_cancel_task_registered(self):
        assert "cancel_task" in ACTIONS


class TestScheduleTask:
    def test_valid_one_time_task(self):
        """Schedule a one-time task with valid params."""
        with patch.object(scheduler_tools, "_TASKS_FILE", Path(tempfile.mktemp(suffix=".json"))):
            with patch.object(scheduler_tools, "_load_tasks", return_value=[]):
                with patch.object(scheduler_tools, "_save_tasks") as mock_save:
                    result = ACTIONS["schedule_task"](
                        prompt="test task",
                        run_at="2026-12-01T09:00:00Z",
                    )
                    assert result.success
                    assert "task_id" in result.data
                    assert result.data["next_run"] == "2026-12-01T09:00:00Z"
                    assert not result.data["recurring"]
                    mock_save.assert_called_once()

    def test_valid_recurring_task(self):
        """Schedule a recurring task with interval_minutes."""
        with patch.object(scheduler_tools, "_load_tasks", return_value=[]):
            with patch.object(scheduler_tools, "_save_tasks"):
                result = ACTIONS["schedule_task"](
                    prompt="check email",
                    run_at="2026-12-01T09:00:00Z",
                    interval_minutes=1440,
                    description="Daily email check",
                )
                assert result.success
                assert result.data["recurring"]

    def test_invalid_datetime(self):
        """Invalid datetime format should return error."""
        result = ACTIONS["schedule_task"](
            prompt="test",
            run_at="not-a-date",
        )
        assert not result.success
        assert "datetime" in result.error.lower() or "invalid" in result.error.lower()


class TestListScheduled:
    def test_empty_list(self):
        """No tasks → empty list."""
        with patch.object(scheduler_tools, "_load_tasks", return_value=[]):
            result = ACTIONS["list_scheduled"]()
            assert result.success
            assert result.data["count"] == 0

    def test_filter_by_status(self):
        """Filter by status=active."""
        mock_tasks = [
            {"id": "a", "status": "active", "prompt": "x", "run_at": "2026-01-01T00:00:00Z"},
            {"id": "b", "status": "cancelled", "prompt": "y", "run_at": "2026-01-01T00:00:00Z"},
        ]
        with patch.object(scheduler_tools, "_load_tasks", return_value=mock_tasks):
            result = ACTIONS["list_scheduled"](status="active")
            assert result.success
            assert result.data["count"] == 1
            assert result.data["tasks"][0]["id"] == "a"

    def test_prompt_truncated(self):
        """Prompt should be truncated in list output for privacy."""
        long_prompt = "x" * 500
        mock_tasks = [{"id": "a", "status": "active", "prompt": long_prompt, "run_at": "2026-01-01T00:00:00Z"}]
        with patch.object(scheduler_tools, "_load_tasks", return_value=mock_tasks):
            result = ACTIONS["list_scheduled"]()
            assert result.success
            preview = result.data["tasks"][0]["prompt_preview"]
            assert len(preview) <= 80


class TestCancelTask:
    def test_cancel_existing(self):
        mock_tasks = [{"id": "abc123", "status": "active", "prompt": "x"}]
        with patch.object(scheduler_tools, "_load_tasks", return_value=mock_tasks):
            with patch.object(scheduler_tools, "_save_tasks") as mock_save:
                result = ACTIONS["cancel_task"]("abc123")
                assert result.success
                assert mock_tasks[0]["status"] == "cancelled"
                mock_save.assert_called_once()

    def test_cancel_nonexistent(self):
        with patch.object(scheduler_tools, "_load_tasks", return_value=[]):
            result = ACTIONS["cancel_task"]("nonexistent")
            assert not result.success
            assert "not found" in result.error.lower()
