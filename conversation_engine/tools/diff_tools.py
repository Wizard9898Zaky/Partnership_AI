"""
conversation_engine/tools/diff_tools.py
File watching and diff awareness actions for Partnership_AI.

Actions:
  watch_file       — Start tracking a file for changes (stores a SHA-256 baseline)
  check_file_diff  — Compare a file's current content against its stored baseline
  list_watched     — List all files currently being watched

The watcher stores SHA-256 hashes of file contents in a JSON state file.
When check_file_diff is called, it compares the current hash to the baseline
and reports whether the file changed, including a unified diff of the content.

No external dependencies — uses only stdlib hashlib, difflib, and json.
"""
from __future__ import annotations
from typing import Any, Dict, List
from pathlib import Path
import json
import hashlib
import difflib
import logging

from conversation_engine.action_registry import (
    ActionResult, register_action, safe_resolve_path, load_whitelist,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_WATCH_FILE = Path(__file__).resolve().parent.parent.parent / "cr_logs" / "file_watch_state.json"
_MAX_WATCHED = 100


# ─────────────────────────────────────────────────────────────────────────────
# State management
# ─────────────────────────────────────────────────────────────────────────────

def _load_watch_state() -> Dict:
    """Load the watch state from disk. Returns {} if file is missing or corrupt."""
    try:
        if not _WATCH_FILE.exists():
            return {}
        data = json.loads(_WATCH_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_watch_state(state: Dict) -> None:
    """Atomically save the watch state to disk."""
    _WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _WATCH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(_WATCH_FILE)


def _file_sha256(path: Path) -> str:
    """
    Compute the SHA-256 hash of a file's contents.

    Args:
        path: Path to the file to hash.

    Returns:
        Hex digest string, or empty string on error.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════════════════

@register_action(
    "watch_file",
    input_schema={
        "type": "object",
        "required": ["file_path"],
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to watch"},
        },
    },
)
def watch_file_action(file_path: str) -> ActionResult:
    """
    Start watching a file for changes.

    Stores a SHA-256 hash baseline of the file's current content.
    Use ``check_file_diff`` later to see if it has changed.

    Args:
        file_path: Path to the file to watch (must be whitelisted).

    Returns:
        ActionResult with data['file'] and data['hash'] (first 16 chars).
    """
    try:
        path = safe_resolve_path(file_path)
        if not path.exists():
            return ActionResult(success=False, error=f"File not found: {file_path}")

        state = _load_watch_state()

        if len(state) >= _MAX_WATCHED and file_path not in state:
            return ActionResult(
                success=False,
                error=f"Maximum watched files ({_MAX_WATCHED}) reached.",
            )

        content_hash = _file_sha256(path)
        state[file_path] = {
            "hash": content_hash,
            "size": path.stat().st_size,
            "watched_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        }
        _save_watch_state(state)

        return ActionResult(
            success=True,
            data={
                "file": file_path,
                "hash": content_hash[:16],
                "message": f"Now watching '{file_path}'. Use check_file_diff to detect changes.",
            },
        )

    except Exception as e:
        logger.exception("watch_file failed")
        return ActionResult(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════════════════════

@register_action(
    "check_file_diff",
    input_schema={
        "type": "object",
        "required": ["file_path"],
        "properties": {
            "file_path": {"type": "string", "description": "Path to the watched file"},
        },
    },
)
def check_file_diff_action(file_path: str) -> ActionResult:
    """
    Check if a watched file has changed since it was watched.

    Compares the current content hash against the stored baseline.
    If the file changed, returns a unified diff of the changes.

    Args:
        file_path: Path to the file to check (must have been watched first).

    Returns:
        ActionResult with data['changed'] (bool), data['diff'] (unified diff text),
        and data['old_hash'] / data['new_hash'].
    """
    try:
        path = safe_resolve_path(file_path)
        state = _load_watch_state()

        if file_path not in state:
            return ActionResult(
                success=False,
                error=f"File '{file_path}' is not being watched. Call watch_file first.",
            )

        old_entry = state[file_path]
        old_hash = old_entry["hash"]
        new_hash = _file_sha256(path)

        if old_hash == new_hash:
            return ActionResult(
                success=True,
                data={
                    "file": file_path,
                    "changed": False,
                    "message": "No changes detected.",
                },
            )

        # File changed — generate a diff
        old_content = old_entry.get("content_snapshot", "")

        # If we don't have a snapshot (legacy), just report the change without diff
        diff_text = ""
        if old_content:
            old_lines = old_content.splitlines(keepends=True)
            new_lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            diff_text = "".join(
                difflib.unified_diff(
                    old_lines, new_lines,
                    fromfile=f"{file_path} (baseline)",
                    tofile=f"{file_path} (current)",
                    n=3,
                )
            )

        # Update the baseline to the new content
        new_content = path.read_text(encoding="utf-8", errors="replace")
        state[file_path] = {
            "hash": new_hash,
            "size": path.stat().st_size,
            "content_snapshot": new_content[:50000],  # cap at 50KB
            "updated_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        }
        _save_watch_state(state)

        return ActionResult(
            success=True,
            data={
                "file": file_path,
                "changed": True,
                "old_hash": old_hash[:16],
                "new_hash": new_hash[:16],
                "diff": diff_text[:10000] if diff_text else "(no baseline content to diff against)",
                "message": f"File '{file_path}' has changed.",
            },
        )

    except Exception as e:
        logger.exception("check_file_diff failed")
        return ActionResult(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════════════════════

@register_action(
    "list_watched",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
def list_watched_action() -> ActionResult:
    """
    List all files currently being watched for changes.

    Returns:
        ActionResult with data['files'] = list of {file, hash, size, watched_at}.
    """
    try:
        state = _load_watch_state()
        files = [
            {
                "file": fp,
                "hash": entry.get("hash", "")[:16],
                "size": entry.get("size", 0),
                "watched_at": entry.get("watched_at", entry.get("updated_at", "")),
            }
            for fp, entry in state.items()
        ]

        return ActionResult(
            success=True,
            data={"files": files, "count": len(files)},
        )

    except Exception as e:
        return ActionResult(success=False, error=str(e))
