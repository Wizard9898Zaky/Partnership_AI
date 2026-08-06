#!/usr/bin/env python3
# conversation_engine/summarizer.py
"""
Summarizer — Groq-backed text summarization and prompt processing.

Token budget rationale (v1.2):
─────────────────────────────
Different call sites have very different output needs:

  TASK                              TOKENS NEEDED
  ──────────────────────────────────────────────
  Simple conversational summary     256–512
  Intent routing (single word out)  64
  Self-reflection JSON              256–512
  Identity extraction (full JSON)   1024–2048
  Multi-step plan generation        1024–2048
  Ethics LLM second-pass            512–768

The old hardcoded 256 cap silently truncated identity extraction and plan
generation — callers got partial JSON that failed to parse, with no error.

Fix: Default raised to 1024 (covers all common cases). Callers that need
more (identity extraction, long plans) can pass max_tokens explicitly.
The env var GROQ_SUMMARIZER_TOKENS provides a global override without
touching code.
"""

import logging
import os
from groq_backend import generate_response

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ─────────────────────────────────────────────────────────────────────────────
# Token defaults
# Default: 1024 — safe for reflection, routing, and most extraction tasks.
# Override globally:  export GROQ_SUMMARIZER_TOKENS=2048
# Override per-call:  summarizer.summarize(text, max_tokens=2048)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_MAX_TOKENS = int(os.getenv("GROQ_SUMMARIZER_TOKENS", "1024"))


class Summarizer:
    """
    A summarizer / prompt processor that uses the Groq backend.

    Call sites across Partnership_AI use this for:
      - Conversational summaries
      - Intent routing (single-word output)
      - Self-reflection JSON generation
      - Identity / memory extraction (needs high token budget)
      - Ethics LLM second-pass review
      - Multi-step plan narration

    Pass max_tokens explicitly when you know the output will be large
    (e.g. identity extraction should pass max_tokens=2048).
    """

    def __init__(self, model: str = None):
        self.model = model or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    def summarize(
        self,
        text: str,
        instruction: str = "Summarize this text clearly and concisely.",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.3,
    ) -> str:
        """
        Send a prompt to the Groq backend and return the response.

        Args:
            text:        The content or full prompt to process.
            instruction: Prepended directive (ignored if text is already a
                         complete prompt — callers that build their own prompt
                         can pass instruction="" to skip the prefix).
            max_tokens:  Output token limit. Override for large outputs like
                         identity extraction (2048) or short routing (64).
            temperature: Sampling temperature. Lower = more deterministic.

        Returns:
            Stripped response string from the LLM.
        """
        if not text or not text.strip():
            raise ValueError("'text' must be a non-empty string.")

        # Only prepend the instruction separator if there is an instruction.
        if instruction:
            prompt = (
                f"{instruction}\n\n"
                f"───\n\n"
                f"{text}\n\n"
                f"───\n\n"
                f"Return only the result, no explanations or preambles."
            )
        else:
            prompt = text

        try:
            summary = generate_response(
                prompt,
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except TypeError:
            # Fallback for a simplified backend signature (no kwargs support)
            logger.info("generate_response() does not accept kwargs. Using fallback call.")
            summary = generate_response(prompt)

        return summary.strip()
