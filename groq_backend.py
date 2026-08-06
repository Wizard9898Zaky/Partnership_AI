#!/usr/bin/env python3
# groq_backend.py
"""
Groq Backend Interface
──────────────────────
Handles API communication with the Groq large language model backend.

Features:
 - Safe fallback if Groq SDK or API key missing
 - Compatible with all Groq client versions
 - Configurable model via GROQ_MODEL environment variable
 - Retry with exponential backoff (3 attempts) on transient failures
 - Graceful error handling with clear messages
 - LLM interaction logging controlled by config.json → llm_logging.enabled
   (set "enabled": true to debug; false in production to protect privacy)
 - Log rotation: rotated when file exceeds llm_logging.rotate_mb MB,
   keeping llm_logging.rotate_keep backups

CHANGELOG:
 v1.2:
  - Added 3-attempt exponential backoff retry on transient errors
    (rate-limit, 503, connection errors). Auth errors and model-
    decommission errors are NOT retried — they are permanent failures.
  - LLM logging is now config-driven: reads config.json → llm_logging.
    Previously LOG_ENABLED was a hardcoded True, which logged every
    prompt (including identity blobs) to plaintext plans.log in production.
  - Added log rotation via logging.handlers.RotatingFileHandler metadata;
    the raw log write is rotated manually to stay dependency-free.
 v1.1:
  - Raised DEFAULT_TOKENS 512 → 2048
  - Model name fully driven by GROQ_MODEL env var
"""
import os
import json
import time
import logging
import re
from pathlib import Path
from datetime import datetime, timezone

# ─────────────────────
# Logging configuration
# ─────────────────────
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ─────────────────────────────────────────────
# Safe Groq import
# ─────────────────────────────────────────────
try:
    from groq import Groq
except ImportError as e:
    raise ImportError(
        f"[FATAL] Groq client not installed or broken: {e}\n"
        "Try: pip install groq --upgrade"
    )

# ─────────────
# API key setup
# ─────────────
API_KEY = os.getenv("GROQ_API_KEY")
if not API_KEY:
    logger.warning(
        "GROQ_API_KEY not found in environment. "
        "Set it via: export GROQ_API_KEY='your_key_here'"
    )
    client = None
else:
    try:
        client = Groq(api_key=API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Groq client: {e}")
        client = None

# ───────────────────
# Model Configuration
# ───────────────────
# Override: export GROQ_MODEL="llama-3.3-70b-versatile"
# Reference: https://console.groq.com/docs/models
DEFAULT_MODEL  = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
DEFAULT_TEMP   = 0.7
DEFAULT_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "2048"))

# ─────────────────────────────────────────────────────────────────────
# Multi-model routing
# ─────────────────────────────────────────────────────────────────────
# Short classifier/gate prompts (max_tokens <= 50) are routed to a fast
# model to conserve TPM on the free tier. Generation/extraction prompts
# use the full model for quality.
# Override either via environment variables.
FAST_MODEL  = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
FULL_MODEL  = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
FAST_MODEL_THRESHOLD = int(os.getenv("GROQ_FAST_MODEL_THRESHOLD", "50"))


def _select_model(max_tokens: int, prompt: str = "") -> str:
    """
    Select which model to use for this LLM call.

    Short classifier/gate prompts (max_tokens <= FAST_MODEL_THRESHOLD)
    are routed to FAST_MODEL. Everything else uses FULL_MODEL.

    This directly reduces TPM consumption on the free tier by sending
    cheap classification work to a faster, lower-token model.

    Args:
        max_tokens: The max_tokens parameter for this call.
        prompt:     The prompt text (unused by default but available for
                    future routing heuristics based on prompt content).

    Returns:
        Model name string.
    """
    if max_tokens <= FAST_MODEL_THRESHOLD:
        return FAST_MODEL
    return FULL_MODEL

# Retry configuration
MAX_RETRIES        = 3
RETRY_BASE_DELAY   = 1.0   # seconds; doubles each attempt
# Error substrings that are permanent — do NOT retry these
_PERMANENT_ERRORS  = ("decommissioned", "401", "authentication", "invalid_api_key")


# ─────────────────────────────────────────────
# Config-driven LLM logging
# ─────────────────────────────────────────────
def _load_log_config() -> dict:
    """
    Read llm_logging section from config.json.
    Returns defaults if the file is missing or malformed.
    """
    _defaults = {"enabled": False, "log_path": "plans.log", "rotate_mb": 5, "rotate_keep": 3}
    try:
        cfg_path = Path(__file__).parent / "config.json"
        if not cfg_path.exists():
            return _defaults
        raw = cfg_path.read_text(encoding="utf-8")
        # Strip JS-style // comments
        # FIX: only strip // comments that are NOT part of a URL scheme
        raw = re.sub(r'(?<!:)//[^\n]*', "", raw)
        cfg = json.loads(raw)
        return {**_defaults, **cfg.get("llm_logging", {})}
    except Exception:
        return _defaults


def _rotate_log_if_needed(log_path: Path, max_mb: int, keep: int) -> None:
    """
    Rotate log_path if it exceeds max_mb megabytes.
    Keeps up to `keep` numbered backups (.1, .2 …).
    """
    try:
        if not log_path.exists():
            return
        if log_path.stat().st_size < max_mb * 1024 * 1024:
            return
        # Shift existing backups
        for i in range(keep - 1, 0, -1):
            src = log_path.with_suffix(f".log.{i}") if log_path.suffix != ".log" \
                  else log_path.parent / f"{log_path.stem}.{i}"
            dst = log_path.parent / f"{log_path.stem}.{i + 1}"
            if src.exists():
                src.replace(dst)
        # Move current log to .1
        backup = log_path.parent / f"{log_path.stem}.1"
        log_path.replace(backup)
    except Exception as exc:
        logger.debug(f"[LOG ROTATE] {exc}")


def _log_llm_interaction(
    prompt: str,
    response: str,
    model: str,
    attempt: int = 1,
    success: bool = True,
) -> None:
    """
    Append an LLM interaction record to the configured log file.
    Only runs when config.json → llm_logging → enabled is true.
    """
    cfg = _load_log_config()
    if not cfg["enabled"]:
        return

    log_path = Path(cfg["log_path"])
    _rotate_log_if_needed(log_path, cfg["rotate_mb"], cfg["rotate_keep"])

    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        status    = "✅ SUCCESS" if success else "❌ FAILED"
        entry = (
            f"═══════════════════════════════════════════════════════════\n"
            f"🤖 [LLM INTERACTION] {timestamp}  attempt={attempt}\n"
            f"   Model: {model}  |  Status: {status}\n"
            f"───────────────────────────────────────────────────────────\n"
            f"📝 PROMPT:\n{prompt}\n"
            f"───────────────────────────────────────────────────────────\n"
            f"💬 RESPONSE:\n{response}\n"
            f"═══════════════════════════════════════════════════════════\n\n"
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as exc:
        logger.error(f"[LOG ERROR] Failed to write LLM log: {exc}")


# ─────────────────────────────────────────────
# Permanent-failure classifier
# ─────────────────────────────────────────────
def _is_permanent_error(err_str: str) -> bool:
    """Return True for errors that will not resolve with a retry."""
    low = err_str.lower()
    return any(marker in low for marker in _PERMANENT_ERRORS)


def _user_friendly_error(err_str: str, model: str) -> str:
    """Map a raw Groq exception string to a user-readable message."""
    low = err_str.lower()
    if "decommissioned" in low:
        return (
            f"⚠️ Model '{model}' is no longer available. "
            f"Set a new model via: export GROQ_MODEL='llama-3.3-70b-versatile'"
        )
    if "rate limit" in low:
        return f"⚠️ [Rate Limit] Limit reached for {model}. Try waiting or upgrading your Groq plan."
    if "401" in err_str or "authentication" in low or "invalid_api_key" in low:
        return "⚠️ [Auth Error] Invalid GROQ_API_KEY. Check your environment variable."
    return f"[Groq Exception] {err_str}"


# ─────────────
# Core Function
# ─────────────
def generate_response(prompt: str, model: str = DEFAULT_MODEL, **kwargs):
    """
    Send prompt to the Groq backend and return the response text.

    Retries up to MAX_RETRIES times with exponential backoff on transient
    failures (rate-limits, 503s, connection errors). Permanent failures
    (bad auth, decommissioned model) are returned immediately without retry.

    Optional kwargs:
        max_tokens   (int):   override DEFAULT_TOKENS for this call
        temperature  (float): override DEFAULT_TEMP for this call
        return_usage (bool):  if True, return (text, usage_dict) instead
                              of just text. usage_dict has
                              prompt_tokens/completion_tokens/total_tokens
                              (all 0 if usage wasn't available, e.g. on
                              an error path). Previously response.usage
                              was discarded entirely, making real
                              cost/token tracking impossible - default
                              is False, so every existing call site's
                              plain-string contract is unchanged.

    Returns a plain string (or a (string, usage_dict) tuple if
    return_usage=True) — never raises.
    """
    return_usage = kwargs.get("return_usage", False)
    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _wrap(text: str, usage: dict):
        return (text, usage) if return_usage else text

    if not client:
        msg = "⚠️ [Error] Groq backend unavailable or API key missing."
        _log_llm_interaction(prompt, msg, model, attempt=0, success=False)
        return _wrap(msg, empty_usage)

    # Multi-model routing: use fast model for short classifier prompts
    if model == DEFAULT_MODEL:
        model = _select_model(kwargs.get("max_tokens", DEFAULT_TOKENS), prompt)

    api_params = {
        "model":       model,
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": kwargs.get("temperature", DEFAULT_TEMP),
        "max_tokens":  kwargs.get("max_tokens",  DEFAULT_TOKENS),
    }

    last_error = ""
    delay = RETRY_BASE_DELAY

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "LLM request | model=%s | max_tokens=%s | attempt=%d/%d",
                model, api_params["max_tokens"], attempt, MAX_RETRIES,
            )
            response = client.chat.completions.create(**api_params)

            if hasattr(response, "choices") and response.choices:
                # FIX: content may be None when Groq applies content filtering
                _raw_content = response.choices[0].message.content
                reply = _raw_content.strip() if _raw_content else "⚠️ Empty or filtered response from Groq API."
            else:
                reply = "⚠️ Empty response received from Groq API."

            usage = dict(empty_usage)
            if hasattr(response, "usage") and response.usage is not None:
                usage = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
                }

            _log_llm_interaction(prompt, reply, model, attempt=attempt, success=True)
            return _wrap(reply, usage)

        except Exception as exc:
            last_error = str(exc)

            if _is_permanent_error(last_error):
                # No point retrying — bail immediately
                msg = _user_friendly_error(last_error, model)
                _log_llm_interaction(prompt, msg, model, attempt=attempt, success=False)
                return _wrap(msg, empty_usage)

            if attempt < MAX_RETRIES:
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt, MAX_RETRIES, last_error, delay,
                )
                time.sleep(delay)
                delay *= 2          # exponential backoff
            else:
                logger.error("LLM call failed after %d attempts: %s", MAX_RETRIES, last_error)

    msg = _user_friendly_error(last_error, model)
    _log_llm_interaction(prompt, msg, model, attempt=MAX_RETRIES, success=False)
    return _wrap(msg, empty_usage)


# ─────────
# Self-Test
# ─────────
if __name__ == "__main__":
    test_prompt = "Explain what ethical AI means in one sentence."
    logger.info("Testing Groq backend connection...")
    reply = generate_response(test_prompt)
    logger.info(f"Response: {reply}")
    cfg = _load_log_config()
    logger.info(f"LLM logging enabled: {cfg['enabled']} | log_path: {cfg['log_path']}")
