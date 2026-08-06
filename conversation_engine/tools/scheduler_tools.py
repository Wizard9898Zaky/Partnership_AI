"""
conversation_engine/tools/scheduler_tools.py
Scheduled / background task actions for Partnership_AI.

Actions:
  schedule_task    — Schedule a one-time or recurring task that fires the
                     agent with a stored prompt at the specified time/interval.
  list_scheduled   — List all scheduled tasks.
  cancel_task      — Cancel a scheduled task by ID.

The scheduler uses a lightweight JSON file (cr_logs/scheduled_tasks.json) for
persistence. A background thread checks for due tasks every 30 seconds and
fires them by calling the agent's run() method with the stored prompt.

No external dependencies — uses only stdlib threading, json, and time.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pathlib import Path
from dataclasses import dataclass, asdict
import json
import time
import threading
import logging
import uuid
from datetime import datetime, timezone

from conversation_engine.action_registry import ActionResult, register_action

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_TASKS_FILE = Path(__file__).resolve().parent.parent.parent / "cr_logs" / "scheduled_tasks.json"
_CHECK_INTERVAL = 30  # seconds between checks
_MAX_TASKS = 50        # safety limit

# ─────────────────────────────────────────────────────────────────────────────
# Task storage
# ─────────────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_watcher_thread: Optional[threading.Thread] = None
_agent_callback = None  # set by new_main_chat.py at startup


def set_agent_callback(fn) -> None:
    """
    Register the function that scheduled tasks will call when they fire.

    This must be called once at startup (from new_main_chat.py) and passed
    a callable that accepts a single ``prompt: str`` argument and returns a
    response string. Typically this is ``agent.run`` wrapped with
    DialogueEngine response generation.

    Args:
        fn: A callable(prompt: str) -> str that processes the scheduled prompt.
    """
    global _agent_callback
    _agent_callback = fn
    logger.info("[Scheduler] Agent callback registered.")


def _load_tasks() -> List[Dict]:
    """Load scheduled tasks from the JSON file. Returns [] if file is missing/corrupt."""
    try:
        if not _TASKS_FILE.exists():
            return []
        data = json.loads(_TASKS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except Exception:
        logger.warning("[Scheduler] Could not load scheduled_tasks.json — starting fresh.")
        return []


def _save_tasks(tasks: List[Dict]) -> None:
    """Atomically save tasks to the JSON file."""
    _TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TASKS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    tmp.replace(_TASKS_FILE)


def _compute_next_run(task: Dict) -> Optional[str]:
    """
    Compute the next ISO-8601 run time for a task.

    For one-time tasks, returns the stored run_at time.
    For recurring tasks, computes the next occurrence based on interval_minutes.

    Args:
        task: Task dict with 'run_at' (ISO string) and optional 'interval_minutes'.

    Returns:
        ISO-8601 timestamp string, or None if the task has expired.
    """
    now = datetime.now(timezone.utc)

    if task.get("interval_minutes"):
        # Recurring task — compute next run from last_run or run_at
        last = task.get("last_run") or task["run_at"]
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        interval = float(task["interval_minutes"])
        while last_dt < now:
            last_dt = last_dt.fromtimestamp(
                last_dt.timestamp() + interval * 60,
                tz=timezone.utc,
            )
        return last_dt.isoformat()
    else:
        # One-time task
        run_at = datetime.fromisoformat(task["run_at"].replace("Z", "+00:00"))
        if run_at < now and not task.get("last_run"):
            return task["run_at"]  # overdue — fire immediately
        if task.get("last_run"):
            return None  # already ran, don't repeat
        return task["run_at"]


def _watcher_loop() -> None:
    """
    Background thread loop that checks for due tasks and fires them.

    Runs every _CHECK_INTERVAL seconds. Calls _agent_callback with the
    task's prompt when a task is due, then updates last_run.
    """
    logger.info("[Scheduler] Background watcher started.")
    while True:
        try:
            tasks = _load_tasks()
            now = datetime.now(timezone.utc)
            changed = False

            for task in tasks:
                if task.get("status") != "active":
                    continue

                next_run_str = _compute_next_run(task)
                if not next_run_str:
                    continue

                next_run = datetime.fromisoformat(next_run_str.replace("Z", "+00:00"))
                if next_run > now:
                    continue

                # Task is due — fire it
                prompt = task.get("prompt", "")
                task_id = task.get("id", "unknown")
                logger.info("[Scheduler] Firing task %s: %s", task_id, prompt[:80])

                if _agent_callback:
                    try:
                        response = _agent_callback(prompt)
                        logger.info("[Scheduler] Task %s completed. Response: %s",
                                    task_id, str(response)[:100])
                    except Exception as e:
                        logger.error("[Scheduler] Task %s failed: %s", task_id, e)

                task["last_run"] = now.isoformat()
                task["run_count"] = task.get("run_count", 0) + 1

                # For one-time tasks, mark as completed
                if not task.get("interval_minutes"):
                    task["status"] = "completed"

                changed = True

            if changed:
                _save_tasks(tasks)

        except Exception as e:
            logger.error("[Scheduler] Watcher loop error: %s", e)

        time.sleep(_CHECK_INTERVAL)


def start_watcher() -> None:
    """
    Start the background task watcher thread (daemon).

    Safe to call multiple times — if the thread is already running, this is a no-op.
    Called from new_main_chat.py at startup.
    """
    global _watcher_thread
    if _watcher_thread is not None and _watcher_thread.is_alive():
        return
    _watcher_thread = threading.Thread(target=_watcher_loop, daemon=True, name="scheduler-watcher")
    _watcher_thread.start()
    logger.info("[Scheduler] Watcher thread started.")


# ═══════════════════════════════════════════════════════════════════════════════
# Action: schedule_task
# ═══════════════════════════════════════════════════════════════════════════════

@register_action(
    "schedule_task",
    input_schema={
        "type": "object",
        "required": ["prompt", "run_at"],
        "properties": {
            "prompt": {"type": "string", "description": "The prompt to send to the agent when the task fires"},
            "run_at": {"type": "string", "description": "ISO-8601 datetime (e.g. 2026-08-01T09:00:00Z)"},
            "interval_minutes": {"type": "number", "description": "If set, task repeats every N minutes"},
            "description": {"type": "string", "description": "Optional human-readable description"},
        },
    },
)
def schedule_task_action(
    prompt: str,
    run_at: str,
    interval_minutes: Optional[float] = None,
    description: str = "",
) -> ActionResult:
    """
    Schedule a one-time or recurring background task.

    The task fires the agent with the stored prompt at the specified time.
    For recurring tasks, set interval_minutes to the repeat interval.

    Args:
        prompt:           The prompt to send to the agent when the task fires.
        run_at:           ISO-8601 datetime string (UTC recommended, e.g. '2026-08-01T09:00:00Z').
        interval_minutes: If set, the task repeats every N minutes (e.g. 1440 = daily).
        description:      Optional human-readable description of the task.

    Returns:
        ActionResult with data['task_id'] and data['next_run'].
    """
    try:
        tasks = _load_tasks()
        if len(tasks) >= _MAX_TASKS:
            return ActionResult(success=False, error=f"Maximum tasks ({_MAX_TASKS}) reached.")

        # Validate the datetime
        try:
            dt = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
        except ValueError:
            return ActionResult(success=False, error=f"Invalid datetime format: {run_at}")

        task = {
            "id": uuid.uuid4().hex[:12],
            "prompt": prompt[:2000],
            "run_at": run_at,
            "interval_minutes": interval_minutes,
            "description": description[:200],
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_run": None,
            "run_count": 0,
        }

        with _lock:
            tasks.append(task)
            _save_tasks(tasks)

        # Start watcher if not running
        start_watcher()

        return ActionResult(
            success=True,
            data={
                "task_id": task["id"],
                "next_run": run_at,
                "recurring": interval_minutes is not None,
                "message": f"Task scheduled for {run_at}.",
            },
        )

    except Exception as e:
        logger.exception("schedule_task failed")
        return ActionResult(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════════════════════

@register_action(
    "list_scheduled",
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Filter by status: active, completed, cancelled"},
        },
    },
)
def list_scheduled_action(status: str = "") -> ActionResult:
    """
    List all scheduled tasks, optionally filtered by status.

    Args:
        status: Filter by status ('active', 'completed', 'cancelled'). Empty = all.

    Returns:
        ActionResult with data['tasks'] = list of task dicts.
    """
    try:
        tasks = _load_tasks()
        if status:
            tasks = [t for t in tasks if t.get("status") == status]

        # Don't return the full prompt for privacy — truncate
        summary = [
            {
                "id": t.get("id"),
                "description": t.get("description", ""),
                "run_at": t.get("run_at"),
                "interval_minutes": t.get("interval_minutes"),
                "status": t.get("status"),
                "last_run": t.get("last_run"),
                "run_count": t.get("run_count", 0),
                "prompt_preview": t.get("prompt", "")[:80],
            }
            for t in tasks
        ]

        return ActionResult(
            success=True,
            data={"tasks": summary, "count": len(summary)},
        )

    except Exception as e:
        return ActionResult(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════════════════════

@register_action(
    "cancel_task",
    input_schema={
        "type": "object",
        "required": ["task_id"],
        "properties": {
            "task_id": {"type": "string", "description": "The task ID to cancel"},
        },
    },
)
def cancel_task_action(task_id: str) -> ActionResult:
    """
    Cancel a scheduled task by its ID.

    Args:
        task_id: The 12-character task ID returned by schedule_task.

    Returns:
        ActionResult confirming cancellation.
    """
    try:
        tasks = _load_tasks()
        found = False
        with _lock:
            for t in tasks:
                if t.get("id") == task_id:
                    t["status"] = "cancelled"
                    found = True
                    break
            if found:
                _save_tasks(tasks)

        if not found:
            return ActionResult(success=False, error=f"Task '{task_id}' not found.")

        return ActionResult(
            success=True,
            data={"task_id": task_id, "message": "Task cancelled."},
        )

    except Exception as e:
        return ActionResult(success=False, error=str(e))
