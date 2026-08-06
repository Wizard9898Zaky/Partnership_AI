"""
conversation_engine/tools/agent_tools.py
Agent-level actions: direct responses and capability change requests (CR pipeline).
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import json
import logging
from datetime import datetime
from pathlib import Path

from conversation_engine.action_registry import (
    ActionResult, register_action,
)
ROOT = Path(__file__).resolve().parent.parent.parent  # project root

logger = logging.getLogger(__name__)

@register_action(
    "respond_to_user",
    input_schema={
        "type": "object",
        "required": ["message"],
        "properties": {
            "message": {"type": "string"}
        },
    },
)
def respond_to_user_action(message: str) -> ActionResult:
    """Action: return a message string directly to the user.

    Args:
        message: The response text to deliver to the user.

    Returns:
        ActionResult with data["response"] set to the message string.
    """
    try:
        return ActionResult(
            success=True,
            data={"response": str(message) if message is not None else ""},
        )
    except Exception as e:
        return ActionResult(success=False, error=str(e))

@register_action(
    "request_change",
    input_schema={
        "type": "object",
        "required": ["capability", "reasoning"],
        "properties": {
            "capability": {"type": "string"},
            "reasoning": {"type": "string"},
        },
    },
)
def request_change_action(capability: str, reasoning: str) -> ActionResult:
    """
    Request a system evolution or capability.
    IMPORTANT: This should be called via
    AdaptiveAgent instance which overrides this
    with _trigger_evolution(). If called directly,
    it logs a change request file.
    """
    try:
        cr_dir = ROOT / "cr_logs"
        cr_dir.mkdir(exist_ok=True)
        timestamp = datetime.now()
        filename = f"CR_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
        path = cr_dir / filename
        data = {
            "timestamp": timestamp.isoformat(),
            "capability_requested": capability,
            "reasoning": reasoning,
            "status": "PENDING_APPROVAL",
            "source": "direct_action_call"
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return ActionResult(
            success=True,
            data={
                "status": "logged",
                "capability": capability,
                "file": str(path),
                "message": f"Change request logged to {path.name}. "
                          f"Note: For full evolution, use via AdaptiveAgent instance."
            }
        )
    except Exception as e:
        logger.error(f"Failed to log change request: {e}")
        return ActionResult(
            success=False,
            error=str(e),
            data={"capability": capability, "reasoning": reasoning}
        )

# ──────────────────────
# IDEA INCUBATOR ACTIONS
# ──────────────────────
