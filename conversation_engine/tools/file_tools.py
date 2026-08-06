"""
conversation_engine/tools/file_tools.py
File-system actions: list, read, search, stats, code quality, and code generation.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from pathlib import Path
import re, os

from groq_backend import generate_response as _generate_response

from conversation_engine.action_registry import (
    ActionResult, register_action,
    safe_resolve_path, load_whitelist,
    PolicyEngine, SandboxedExecutor,
)
from utils import local_now as dt_now

@register_action(
    "list_files",
    input_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "limit": {"type": "integer"},
        },
    },
)
def list_files_action(category: str = "all", limit: int = 20) -> ActionResult:
    """Action: list files in the project directory, optionally filtered by category."""
    files = load_whitelist()
    if category != "all":
        files = [f for f in files if f.startswith(category)]
    return ActionResult(
        success=True,
        data={
            "files": files[:limit],
            "count": len(files),
        },
    )

@register_action(
    "read_file",
    input_schema={
        "type": "object",
        "required": ["file_path"],
        "properties": {
            "file_path": {"type": "string"}
        },
    },
)
def read_file_action(file_path: str) -> ActionResult:
    """Action: read and return the contents of a whitelisted file.

    Enforces a 100KB size guard to prevent context window exhaustion.
    """
    try:
        path = safe_resolve_path(file_path)
        if path.stat().st_size > 102_400:
            return ActionResult(
                success=False,
                error=f"File '{file_path}' exceeds the 100KB read limit "
                      f"({path.stat().st_size:,} bytes). Use analyze_code_quality "
                      "or search_code to inspect large files in chunks."
            )
        content = path.read_text(encoding="utf-8")
        return ActionResult(
            success=True,
            data={
                "file": file_path,
                "content": content,
            },
        )
    except Exception as e:
        return ActionResult(False, error=str(e))

@register_action(
    "search_code",
    input_schema={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "file_pattern": {"type": "string"},
        },
    },
)
def search_code_action(
    query: str,
    file_pattern: str = ".py",
) -> ActionResult:
    """Action: search Python source files for a keyword and return matching lines."""
    results = []
    for file in load_whitelist():
        if not file.endswith(file_pattern.replace("*", "")):
            continue
        try:
            path = safe_resolve_path(file)
            content = path.read_text(encoding="utf-8")
            for idx, line in enumerate(content.splitlines(), start=1):
                if query.lower() in line.lower():
                    results.append({
                        "file": file,
                        "line": idx,
                        "snippet": line.strip(),
                    })
        except Exception:
            continue  # Skip unreadable files (permissions, encoding errors, etc.)
    return ActionResult(
        success=True,
        data={
            "query": query,
            "results": results,
            "count": len(results),
        },
    )

@register_action(
    "get_file_stats",
    input_schema={
        "type": "object",
        "required": ["file_path"],
        "properties": {
            "file_path": {"type": "string"}
        },
    },
)
def get_file_stats_action(file_path: str) -> ActionResult:
    """Action: return line count, function count, class count, and import count for a file.

    Enforces a 100KB size guard to prevent memory exhaustion on large files.
    """
    try:
        path = safe_resolve_path(file_path)
        if path.stat().st_size > 102_400:
            return ActionResult(
                success=False,
                error=f"File '{file_path}' exceeds the 100KB limit ({path.stat().st_size:,} bytes)."
            )
        content = path.read_text(encoding="utf-8")
        stats = {
            "lines": len(content.splitlines()),
            "functions": len(re.findall(r"\bdef\s+\w+", content)),
            "classes": len(re.findall(r"\bclass\s+\w+", content)),
            "imports": len(re.findall(r"^\s*(import|from)\s+", content, re.MULTILINE)),
        }
        return ActionResult(True, data=stats)
    except Exception as e:
        return ActionResult(False, error=str(e))

@register_action(
    "analyze_code_quality",
    input_schema={
        "type": "object",
        "required": ["file_path"],
        "properties": {
            "file_path": {"type": "string"}
        },
    },
)
def analyze_code_quality_action(file_path: str) -> ActionResult:
    """Action: parse a Python file and return basic quality metrics."""
    result = read_file_action(file_path)
    if not result.success:
        return result
    content = result.data["content"]
    lines = content.splitlines()
    code_lines = sum(
        1 for l in lines
        if l.strip() and not l.strip().startswith("#")
    )
    todos = [
        l for l in lines
        if "TODO" in l.upper() or "FIXME" in l.upper()
    ]
    return ActionResult(
        success=True,
        data={
            "total_lines": len(lines),
            "code_lines": code_lines,
            "todo_count": len(todos),
            "todos": todos[:5],
        },
    )

@register_action(
    "propose_upgrade",
    input_schema={
        "type": "object",
        "required": ["file_path", "description"],
        "properties": {
            "file_path": {"type": "string"},
            "description": {"type": "string"},
        },
    },
)
def propose_upgrade_action(
    file_path: str,
    description: str,
) -> ActionResult:
    """Action: use the LLM to propose an improvement for a given file."""
    return ActionResult(
        success=True,
        data={
            "status": "proposed",
            "file": file_path,
            "description": description,
            "timestamp": dt_now(),
        },
    )

@register_action(
    "generate_and_write_code",
    input_schema={
        "type": "object",
        "required": ["goal", "filename"],
        "properties": {
            "goal": {"type": "string"},
            "filename": {"type": "string"},
            "description": {"type": "string"},
        },
    },
)
def generate_and_write_code_action(
    goal: str,
    filename: str,
    description: str = "",
) -> ActionResult:
    """Action: generate Python code from a spec and write it to a file."""
    policy = PolicyEngine.validate_file_access(filename)
    if not policy.success:
        return policy
    try:
        prompt = f"""
        Create safe Python code.

        Goal:
        {goal}

        Context:
        {description}
        """
        generated = _generate_response(prompt).strip()
        # Guard: LLM unavailable returns an error string, not code
        # Guard: LLM may return error string instead of code (check broadly)
        _lower = generated.lower()
        if not generated or any(tok in _lower for tok in ("⚠", "[error", "error:", "exception", "unavailable")):
            return ActionResult(
                success=False,
                error=f"LLM returned an error or non-code response: {generated[:120]}"
            )
        executor = SandboxedExecutor(timeout_seconds=10)
        sandbox = executor.execute(generated)
        if not sandbox.get("success"):
            return ActionResult(
                success=False,
                error=f"Sandbox validation failed: {sandbox.get('error')}"
            )
        path = safe_resolve_path(filename, require_whitelist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(generated, encoding="utf-8")
        os.replace(tmp, path)
        return ActionResult(
            success=True,
            data={
                "file": str(path),
                "preview": generated[:500],
            },
        )
    except Exception as e:
        return ActionResult(False, error=str(e))
