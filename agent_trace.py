#!/usr/bin/env python3
# agent_trace.py
"""
Structured observability trace for AdaptiveAgent turns (best-practices
recommendation #12: "make agents observable" - you should be able to
see thoughts/tool calls/errors/decisions/costs/timeline, or you can't
debug or maintain the agent).

Previously the agent had scattered logger.info/warning/exception calls
- useful for live debugging, but no single structured record per turn
of what plan was chosen, which tools were called with what parameters,
whether each succeeded, how long each step took, how many LLM calls
were made, and what the final outcome was.

Writes one JSON object per line to cr_logs/agent_trace.jsonl - simple,
grep-able, loadable into any log viewer without a special format.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app_config import get_config

ROOT = Path(__file__).parent.resolve()
TRACE_PATH = ROOT / "cr_logs" / "agent_trace.jsonl"


def _rotate_trace_if_needed(log_path: Path, max_mb: int, keep: int) -> None:
    """
    Rotate log_path if it exceeds max_mb megabytes, keeping up to `keep`
    numbered backups (.1, .2, ...) - same rotation scheme groq_backend.py
    already uses for plans.log. Previously agent_trace.jsonl had no size
    limit at all and would grow forever.
    """
    try:
        if not log_path.exists():
            return
        if log_path.stat().st_size < max_mb * 1024 * 1024:
            return
        for i in range(keep - 1, 0, -1):
            src = log_path.parent / f"{log_path.stem}.{i}"
            dst = log_path.parent / f"{log_path.stem}.{i + 1}"
            if src.exists():
                src.replace(dst)
        backup = log_path.parent / f"{log_path.stem}.1"
        log_path.replace(backup)
    except Exception:
        pass  # rotation failure must never block a real trace write


class TurnTrace:
    """
    Accumulates one turn's worth of trace data, then writes it as a
    single JSON line via finish(). Usage:

        trace = TurnTrace(user_input)
        ... record_llm_call() / record_step() as they happen ...
        trace.outcome = "completed"   # or "conversation", "schema_validation_failed", etc.
        trace.finish(final_response)
    """

    def __init__(self, user_input: str):
        self.user_input = user_input
        self.started_at = time.monotonic()
        self.started_at_iso = datetime.now(timezone.utc).isoformat()
        self.llm_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.steps: List[Dict[str, Any]] = []
        self.replan_attempts = 0
        self.budget_events: List[str] = []
        self.outcome = "completed"  # updated by the caller as the turn progresses

    def record_llm_call(self, usage: Optional[Dict[str, int]] = None) -> None:
        """Record a single LLM API call within this turn.

        Args:
            model: The Groq model identifier used.
            prompt_tokens: Tokens consumed by the prompt.
            completion_tokens: Tokens in the completion.
            cost_usd: Estimated cost in USD (0.0 if unknown).
            duration_ms: Wall-clock time for the call in milliseconds.
        """
        self.llm_calls += 1
        if usage:
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)

    def estimated_cost_usd(self) -> float:
        """
        Estimate this turn's cost from accumulated token counts, using
        the configurable per-million-token rates in config.json. These
        rates are a point-in-time snapshot (see the _cost_pricing_note
        in config.json) - this is a budgeting aid, not a billing-grade
        figure. Real usage should still be reconciled against your
        actual Groq account dashboard.
        """
        cfg = get_config().get("agent", {})
        input_rate = cfg.get("cost_per_million_input_tokens_usd", 0.15)
        output_rate = cfg.get("cost_per_million_output_tokens_usd", 0.60)
        return (
            (self.prompt_tokens / 1_000_000) * input_rate
            + (self.completion_tokens / 1_000_000) * output_rate
        )

    def record_step(self, action: str, parameters: Dict[str, Any], success: bool,
                     output: str, duration_ms: float, error_type: Optional[str] = None) -> None:
        """Record a single action step executed during this turn.

        Args:
            action: The registered action name.
            output: The action's return value (will be str-cast and truncated).
            success: Whether the action completed without error.
        """
        self.steps.append({
            "action": action,
            "parameters": parameters,
            "success": success,
            "output": (str(output) if output is not None else "")[:500],  # trimmed; cast to str first
            "duration_ms": round(duration_ms, 2),
            "error_type": error_type,
        })

    def record_replan(self) -> None:
        """Increment the replan counter when the agent revises its plan mid-turn.

        Called whenever _replan_actions() produces a new action sequence.
        """
        self.replan_attempts += 1

    def record_budget_event(self, message: str) -> None:
        """Record a budget enforcement event (step limit or LLM call limit hit).

        Args:
            event_type: A short label such as 'step_limit' or 'llm_limit'.
        """
        self.budget_events.append(message)

    def elapsed_seconds(self) -> float:
        """Return elapsed wall-clock seconds since this turn started.

        Uses monotonic time so it is immune to system-clock adjustments.
        """
        return time.monotonic() - self.started_at

    def finish(self, final_response, outcome: Optional[str] = None) -> Dict[str, Any]:
        """Finalise this turn trace, write the record to the JSONL file, and rotate if needed.

        Args:
            final_response: The agent's last response string for this turn.
            outcome: Short label describing the turn result
                ('success', 'exception', 'budget_exceeded', etc.).
        """
        final_str = final_response if isinstance(final_response, str) else str(final_response)
        record = {
            "timestamp": self.started_at_iso,
            "user_input": self.user_input[:500],
            "outcome": outcome or self.outcome,
            "steps": self.steps,
            "steps_count": len(self.steps),
            "steps_succeeded": sum(1 for s in self.steps if s["success"]),
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd(), 6),
            "replan_attempts": self.replan_attempts,
            "budget_events": self.budget_events,
            "total_duration_ms": round(self.elapsed_seconds() * 1000, 2),
            "final_response": (str(final_str) if final_str is not None else "")[:500],
        }
        try:
            TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
            cfg = get_config().get("agent", {})
            _rotate_trace_if_needed(
                TRACE_PATH,
                cfg.get("trace_rotate_mb", 5),
                cfg.get("trace_rotate_keep", 3),
            )
            with open(TRACE_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass  # tracing must never be the reason a real turn fails
        return record


def read_recent_traces(limit: int = 20) -> List[Dict[str, Any]]:
    """Read the most recent N trace entries, oldest first within the returned slice."""
    if not TRACE_PATH.exists():
        return []
    lines = TRACE_PATH.read_text(encoding="utf-8").strip().splitlines()
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue  # Skip malformed/partial lines (can occur during rotation)
    return out
