#!/usr/bin/env python3
# utils.py
"""
Shared utility functions for Partnership_AI.

FIX (v1.1): utc_now() and local_now() previously had trailing commas
on their return statements, causing them to return single-element tuples
instead of strings. This silently broke every caller that used the result
as a string (f-strings, strftime, json.dumps, etc.).
"""

from datetime import datetime, timezone


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string (no trailing comma)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_now() -> str:
    """Return the current local time as an ISO 8601 string with UTC offset."""
    return datetime.now().astimezone().isoformat()


def print_banner(
    name: str = "Partnership_AI",
    tagline: str = "Ethics-Aware AI Assistant",
) -> str:
    """Return a formatted welcome banner string."""
    line = "─" * 50
    name = "WELCOME TO PARTNERSHIP_AI"
    tagline = "The AI That Evolves With You!"
    timestamp = local_now()
    return (
        f"\n{line}\n"
        f"   {name}\n"
        f"   {tagline}\n"
        f"   Initialized at {timestamp}\n"
        f"{line}\n"
    )


def clear_screen() -> None:
    """Clear the terminal screen."""
    print("\033c", end="")
