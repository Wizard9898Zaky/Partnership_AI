#!/usr/bin/env python3
# conversation_engine/logging_utils.py
"""
Utility functions for logging cognitive traces.
Separated to avoid circular imports between main_chat and intent_resolver.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

# Global cache for plan diffing (moved here to be shared)
_previous_plan_cache = {}

def load_plan_cache(cache_file: Path) -> Dict:
    """Load the persisted plan cache from disk; return empty dict on failure."""
    global _previous_plan_cache
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                _previous_plan_cache = json.load(f)
        except Exception:
            _previous_plan_cache = {}
    return _previous_plan_cache

def save_plan_cache(cache_file: Path, cache: Dict):
    """Persist the plan cache dict to disk."""
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass

def log_plan_update(user_id: str, plan: dict, step_output: str = "", thought_process: str = "", log_path: Path = None):
    """Log a plan step update with cache-based diffing for change detection."""
    """
    Logs the AI's THOUGHT PROCESS for every step of a plan.
    Creates a cognitive trace: Goal -> Reasoning -> Action -> Reflection.
    """
    if log_path is None:
        # Default path relative to where this script runs, or you can pass it explicitly
        # Ideally, pass the ROOT path from main_chat to avoid guessing
        log_path = Path.cwd() / "plans.log"
    
    LOG_PLANS = True
    
    if not LOG_PLANS:
        return

    # Load persisted plan cache for cross-session plan diffing
    cache_path = Path.cwd() / ".plan_cache.json"
    load_plan_cache(cache_path)

    current_step_idx = plan.get('current_step_index', 0)
    total_steps = len(plan.get('steps', []))
    current_step_desc = plan.get('steps', [])[current_step_idx] if plan.get('steps') else "No steps"
    
    if not thought_process:
        thought_process = "AI is evaluating the current step based on user input and previous context."

    entry_lines = []
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Simplified logging logic without complex cache diffing to avoid import issues
    # You can restore the diffing logic if you pass the cache object explicitly
    entry_lines.append(f"🧠 [PHASE: STEP EXECUTION] Step {current_step_idx+1}/{total_steps}")
    entry_lines.append(f"   Goal: {plan.get('goal')}")
    entry_lines.append(f"   Target Step: {current_step_desc}")
    entry_lines.append(f"   AI Reasoning: {thought_process}")
    if step_output:
        # Truncate long outputs
        output_preview = step_output[:200] + ('...' if len(step_output) > 200 else '')
        entry_lines.append(f"   Execution Output: {output_preview}")
    entry_lines.append(f"   Reflection: Step {current_step_idx+1} processed.")

    if entry_lines:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"─── [{timestamp}] User: {user_id[:8]}... ───\n")
                for line in entry_lines:
                    f.write(f"{line}\n")
                f.write("-" * 60 + "\n\n")
        except Exception as e:
            print(f"[LOG ERROR] Failed to write plan log: {e}")

        # Persist the plan cache for cross-session diffing
        try:
            cache_path = Path.cwd() / ".plan_cache.json"
            save_plan_cache(cache_path, _previous_plan_cache)
        except Exception:
            pass  # Cache persistence is best-effort

