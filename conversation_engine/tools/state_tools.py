"""
conversation_engine/tools/state_tools.py
Session state: save/load snapshots, system status, capability introspection.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import json, os
from pathlib import Path

from conversation_engine.action_registry import (
    ActionResult, register_action,
    STATE_DIR, save_metadata, metadata_hash,
    ACTIONS, ACTION_SCHEMAS, ACTION_METADATA,
    SANDBOX_REQUIRED,
)
from utils import local_now as dt_now

@register_action(
    "save_state",
    input_schema={
        "type": "object",
        "properties": {
            "snapshot_name": {"type": "string"}
        },
    },
)
def save_state_action(snapshot_name: Optional[str] = None) -> ActionResult:
    """Action: save a state snapshot of the agent's current session."""
    try:
        timestamp = dt_now()
        name = snapshot_name or f"snapshot_{timestamp}"
        path = STATE_DIR / f"{name}.json"
        state = {
            "created": dt_now(),
            "actions": list(ACTIONS.keys()),
            "metadata_hash": metadata_hash(ACTION_METADATA),
        }
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
        return ActionResult(
            success=True,
            data={
                "snapshot": path.name
            },
        )
    except Exception as e:
        return ActionResult(False, error=str(e))

@register_action(
    "load_state",
    input_schema={
        "type": "object",
        "required": ["snapshot_name"],
        "properties": {
            "snapshot_name": {"type": "string"}
        },
    },
)
def load_state_action(snapshot_name: str) -> ActionResult:
    """Action: load a previously saved state snapshot."""
    try:
        path = STATE_DIR / f"{snapshot_name}.json"
        if not path.exists():
            return ActionResult(False, error="Snapshot not found")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ActionResult(True, data=data)
    except Exception as e:
        return ActionResult(False, error=str(e))

@register_action("refresh_metadata")
def refresh_metadata_action() -> ActionResult:
    """Action: refresh and persist the action metadata JSON file.

    Returns:
        ActionResult from save_metadata().
    """
    try:
        return save_metadata()
    except Exception as e:
        return ActionResult(success=False, error=f"Failed to refresh metadata: {e}")

@register_action("get_function_signatures")
def get_function_signatures_action(
    action_name: Optional[str] = None,
) -> ActionResult:
    """Action: extract and return all function signatures from a Python file."""
    if action_name:
        if action_name not in ACTION_METADATA:
            return ActionResult(False, error="Action not found")
        return ActionResult(
            success=True,
            data=ACTION_METADATA[action_name],
        )
    return ActionResult(
        success=True,
        data=ACTION_METADATA,
    )

@register_action("list_capabilities")
def list_capabilities_action() -> ActionResult:
    """Action: return a list of all registered action names and descriptions."""
    try:
        return ActionResult(
            success=True,
            data={"actions": list(ACTIONS.keys())},
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action("get_system_status")
def get_system_status_action() -> ActionResult:
    """Action: return current system status including file counts and ethics state."""
    return ActionResult(
        success=True,
        data={
            "sandbox_required": SANDBOX_REQUIRED,
            "registered_actions": len(ACTIONS),
            "metadata_version": "2.0",
            "state_dir": str(STATE_DIR),
        },
    )
