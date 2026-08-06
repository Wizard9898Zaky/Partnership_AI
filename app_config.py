import copy
#!/usr/bin/env python3
# app_config.py
"""
Centralized configuration loader.

Previously, settings like the sandbox timeout/memory limit and the
ethics threshold were each hardcoded as literals scattered across
sandbox_executor.py, reviewer.py, etc. - config.json existed but was
only actually read by groq_backend.py for one specific section. That
made the real security/behavior posture of the system ("what timeout
is actually enforced, what's the memory ceiling") impossible to see
without grepping every file individually - which is exactly how the
max_memory_mb-was-never-applied bug went unnoticed for as long as it
did.

This module is the single place every other module should get config
from. It's deliberately defensive: if config.json is missing,
corrupted, or missing a key, get_config() falls back to the documented
default for that key rather than raising - a bad config file should
degrade to safe defaults, not crash startup.
"""

import json
import threading
from pathlib import Path
from typing import Any, Dict

_CONFIG_PATH = Path(__file__).parent / "config.json"

# Defaults mirror config.json's shipped values - used if the file is
# missing, unreadable, or missing a specific key/section.
_DEFAULTS: Dict[str, Any] = {
    "logging": {"level": "INFO", "to_file": True, "file_path": "cr_logs/app.log"},
    "ethics": {
        "enabled": True, "threshold": "lenient", "allow_warnings": True,
        "blocked_placeholder": "[warning] The generated answer was blocked by ethics checks.",
    },
    "session": {"dump_on_exit": True, "dump_dir": "cr_logs/session_dumps"},
    "learning": {"enabled": True, "max_batch_size": 500, "min_interval_minutes": 60},
    "fallback": {"use_groq": True, "use_stub": False},
    "llm_logging": {"enabled": True, "log_path": "plans.log", "rotate_mb": 5, "rotate_keep": 3},
    "sandbox": {
        "timeout_seconds": 30, "max_memory_mb": 256, "max_cpu_seconds": 10,
        "use_firejail_if_available": True,
    },
    "reviewer": {
        "smoke_test_timeout_seconds": 30, "run_full_test_suite_on_accept": True,
        "backup_history_limit": 20,
    },
    "agent": {
        "max_plan_steps": 12, "max_runtime_seconds": 60,
        "max_llm_calls_per_turn": 8, "max_replan_attempts": 1,
        "trace_rotate_mb": 5, "trace_rotate_keep": 3,
        "max_cost_usd_per_turn": 0.01,
        "cost_per_million_input_tokens_usd": 0.15,
        "cost_per_million_output_tokens_usd": 0.60,
    },
}

_lock = threading.RLock()  # RLock prevents deadlock on re-entrant get_config calls
_cache: Dict[str, Any] = None


def _deep_merge(base: Dict, override: Dict) -> Dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def get_config(force_reload: bool = False) -> Dict[str, Any]:
    """Return the merged config (file values layered over defaults), cached after first load."""
    global _cache
    with _lock:
        if _cache is not None and not force_reload:
            return _cache
        merged = copy.deepcopy(_DEFAULTS)
        try:
            if _CONFIG_PATH.exists():
                with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                    file_config = json.load(f)
                merged = _deep_merge(_DEFAULTS, file_config)
        except Exception as e:
            # Fall back to defaults rather than crashing startup over a
            # malformed config file - but don't pretend nothing happened.
            print(f"⚠ app_config: could not load config.json ({e}); using built-in defaults")
        _cache = merged
        return merged


def get(section: str, key: str, default: Any = None) -> Any:
    """Convenience accessor: get('sandbox', 'timeout_seconds')."""
    cfg = get_config()
    return cfg.get(section, {}).get(key, default)
