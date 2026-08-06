#!/usr/bin/env python3
# values_kernel/invariants.py
"""
Enforceable invariants for Partnership_AI.

These rules are checked at two enforcement points:
  1. reviewer.py  — before a human approves any Change Request
  2. adaptive_agent.py — before run() processes any user input

Any CR that attempts to modify a protected path is rejected outright.
The kill switch (kill_switch.flag) halts all agent processing immediately
when present — it is checked at runtime by the agent, not just at review time.

IMMUTABLE_PATHS: directories/files the AI may NEVER modify via CR.
KILL_SWITCH_FILE: presence of this file stops the agent entirely.
"""

from pathlib import Path

# ──────────────────────────────────────────
# Protected paths — CR modifications blocked
# ──────────────────────────────────────────
IMMUTABLE_PATHS: list[str] = [
    "values_kernel/",       # Entire ethics core — principles + invariants
    "values_kernel/ethics.json",
    "values_kernel/invariants.py",
    "FOUNDING_PACT.md",     # The covenant cannot be rewritten by the AI
    "kill_switch.flag",     # The kill switch itself is protected
]

# ────────────────────────────────────────────────────────────────
# Kill switch — presence of this file halts all agent processing.
# Create it with: touch kill_switch.flag
# Remove it with: rm kill_switch.flag
# ────────────────────────────────────────────────────────────────
KILL_SWITCH_FILE: Path = Path(__file__).resolve().parent.parent / "kill_switch.flag"


def is_kill_switch_active() -> bool:
    """
    Return True if kill_switch.flag exists in the project root.

    This is checked by AdaptiveAgent.run() before processing any user
    input. When active, the agent refuses all requests and logs the event.
    Create the flag file to trigger an immediate, graceful halt.
    """
    return KILL_SWITCH_FILE.exists()


def check_invariants(cr: dict) -> tuple[bool, str]:
    """
    Validate a Change Request dict against immutable-path rules.

    Args:
        cr: A CR dict. Accepts the actual schema used throughout this
            codebase - a single "file" string key (as used by
            reviewer.py's `log.get("file", "")` and patch_generator.py) -
            as well as a "files" list, for forward compatibility with
            any future multi-file CR format.

    Returns:
        (True, "")              — CR is safe to proceed
        (False, reason_string)  — CR is blocked; reason explains why

    Called by:
        - reviewer.py before presenting a CR for human approval
        - (patch_generator.py enforces the same IMMUTABLE_PATHS list via
          its own _is_under_immutable_dir() helper rather than calling
          this function directly - both read from this module's
          IMMUTABLE_PATHS, so they can't drift out of sync with each
          other.)

    IMPORTANT HISTORY: a previous version of this function only read
    cr.get("files", []) - but every real call site in this codebase
    passes a CR dict with a singular "file" key (a string), never a
    "files" list. That meant changed_files was always empty, the
    validation loop below never ran a single iteration, and this
    function unconditionally returned (True, "") for every real CR -
    silently disabling immutable-path protection in production while
    reviewer.py's UI still displayed a passing invariant check. Caught
    by tests/test_invariants.py (test_check_invariants_blocks_change_to_immutable_path).
    """
    changed_files: list = []
    if "files" in cr and cr["files"]:
        changed_files = list(cr["files"])
    elif cr.get("file"):
        changed_files = [cr["file"]]

    _root = Path(__file__).resolve().parent.parent

    for f in changed_files:
        f_str = str(f)
        # FIX: also compare resolved absolute paths to catch ./values_kernel tricks
        try:
            resolved_f = (_root / f).resolve()
        except Exception:
            resolved_f = None

        for restricted in IMMUTABLE_PATHS:
            # String-level check (fast path)
            if f_str == restricted or f_str.startswith(restricted):
                return (
                    False,
                    f"INVARIANT VIOLATION: CR attempts to modify protected path '{f_str}' "
                    f"(protected by rule: '{restricted}'). "
                    f"This CR cannot be approved.",
                )
            # Resolved path check (catches relative traversals)
            if resolved_f is not None:
                try:
                    resolved_r = (_root / restricted).resolve()
                    if resolved_f == resolved_r or resolved_r in resolved_f.parents:
                        return (
                            False,
                            f"INVARIANT VIOLATION: CR attempts to modify protected path '{f_str}' "
                            f"(resolved match on rule: '{restricted}'). "
                            f"This CR cannot be approved.",
                        )
                except Exception:
                    pass

    return True, ""


def enforce_kill_switch() -> None:
    """
    Raise RuntimeError if the kill switch is active.

    Call this at the top of any entry point that should be halted
    (e.g. AdaptiveAgent.run(), main() in new_main_chat.py).
    """
    if is_kill_switch_active():
        raise RuntimeError(
            "🛑 KILL SWITCH ACTIVE: kill_switch.flag is present. "
            "All agent operations are halted. "
            "Remove the file to resume: rm kill_switch.flag"
        )
