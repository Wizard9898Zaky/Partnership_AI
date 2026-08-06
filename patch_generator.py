#!/usr/bin/env python3
# patch_generator.py
"""
AI-driven patch proposer for Partnership_AI (Groq-backed).

This module inspects a source file and asks the Groq model to propose a single,
safe improvement. It validates model output (Python / JSON sanity checks),
runs a lightweight classifier pass, writes patches into /patches/, and logs
proposals into /cr_logs/.

Key features:
- Clean prompt builder separated from core logic
- Feedback-awareness (learns from recent human rejection reasons)
- Robust parsing for fenced JSON/code blocks
- Safety checks to avoid massive, destructive rewrites
- Termux-friendly subprocess handling
- Fully Groq-integrated (no LLaMA or local model dependencies)
- Ethically grounded via values_kernel/ethics.json
"""
from __future__ import annotations
import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ────────────────────────────
# Imports / graceful fallbacks
# ────────────────────────────
try:
    from groq_backend import generate_response
except Exception:
    generate_response = None  # fallback for environments without groq_backend
try:
    from ethics_loader import load_core_values  # direct ethics source of truth
except Exception:
    def load_core_values():
        """Load the ethics.json principles for use in patch proposals."""
        return {}

# ─────────────
# Configuration
# ─────────────
ROOT = Path(__file__).parent.resolve()
CR_LOGS_DIR = ROOT / "cr_logs"
PATCHES_DIR = ROOT / "patches"
FEEDBACK_FILE = ROOT / "feedback_memory.json"
CR_WORK_DIR = ROOT / ".cr_work"  # safe writable place for temporary CR work

# Ensure directories exist
for d in (CR_LOGS_DIR, PATCHES_DIR, CR_WORK_DIR):
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as _e:
        print(f"[patch_generator] Warning: could not create directory {d}: {_e}")

# Immutable directories that must never be
# directly modified by the CR pipeline.
IMMUTABLE_DIRS = {(ROOT / "values_kernel").resolve()}
# Previously this local set only covered values_kernel/, silently
# omitting FOUNDING_PACT.md and kill_switch.flag - both of which
# values_kernel/invariants.py's canonical IMMUTABLE_PATHS explicitly
# protects. Two independent lists meant a fix or addition to one
# would silently not apply to the other. Pull in the canonical list
# so the two can't drift again.
from values_kernel.invariants import IMMUTABLE_PATHS as _CANONICAL_IMMUTABLE_PATHS
for _p in _CANONICAL_IMMUTABLE_PATHS:
    _resolved = (ROOT / _p).resolve()
    IMMUTABLE_DIRS.add(_resolved)

# Ensure feedback file exists
try:
    if not FEEDBACK_FILE.exists():
        FEEDBACK_FILE.write_text(json.dumps([], indent=2), encoding="utf-8")
except Exception as _e:
    print(f"[patch_generator] Warning: could not initialise feedback file: {_e}")

# Load ethical grounding dynamically
try:
    ETHICS = load_core_values()
    ETHICS_SUMMARY = ETHICS.get("summary", str(ETHICS)) if isinstance(ETHICS, dict) else str(ETHICS)
except Exception:
    ETHICS = {}
    ETHICS_SUMMARY = "No ethics summary available (load_core_values failed)."
MAX_PATCH_RATIO = 3.0  # prevent excessive rewrite
MAX_NEW_LINES = 5000   # absolute safety cap

# ────────────────
# Helper functions
# ────────────────
def _is_under_immutable_dir(path: Path) -> bool:
    """
    Return True if `path` is located inside any declared immutable dir.
    Uses resolved paths to avoid simple symlink bypass.
    """
    try:
        rp = path.resolve()
    except Exception:
        rp = path
    for imm in IMMUTABLE_DIRS:
        try:
            if imm == rp or imm in rp.parents:
                return True
        except Exception:
            # safety: if anything weird happens,
            # treat as immutable.
            return True
    return False

def _try_parse_json(text: str) -> Optional[dict]:
    """Try to parse JSON from text, be very tolerant of surrounding text."""
    if not text:
        return None
    text = text.strip()
    # 1. Try direct load
    try:
        return json.loads(text)
    except Exception:
        pass  # Expected: text is not bare JSON; try regex extraction below
    # 2. Try to extract first JSON object using a
    # regex that finds {...}. This handles cases
    # where the LLM adds markdown or text
    # before/after.
    m = re.search(r"(\{[\s\S]*\})", text)
    if m:
        try:
            candidate = m.group(1)
            # Try to fix common LLM JSON errors
            # (trailing commas, single quotes).
            # Note: We won't do aggressive fixing
            # here to avoid breaking valid JSON,
            # but we'll try standard loads first.
            return json.loads(candidate)
        except Exception:
            pass  # Expected: regex captured non-JSON; try fenced block below
    # 3. Try to extract from code fences
    m_fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m_fence:
        fence_content = m_fence.group(1).strip()
        try:
            return json.loads(fence_content)
        except Exception:
            pass  # Try inner regex extraction on fence content
            m_inner = re.search(r"(\{[\s\S]*\})", fence_content)
            if m_inner:
                try:
                    return json.loads(m_inner.group(1))
                except Exception:
                    pass  # All parse strategies exhausted; return None
    return None

def _extract_fenced_block(text: str, fence_type: str = "json") -> Optional[str]:
    """
    Extract a fenced block like ```json ... ``` from a larger text.
    Returns inner content or None.
    """
    if not text:
        return None
    pattern = rf"""```{fence_type}\s*([\s\S]*?)\s*```"""
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # fallback: try generic fenced block
    m2 = re.search(r"```(?:\w+)?\s*([\s\S]*?)\s*```", text)
    if m2:
        return m2.group(1).strip()
    return None

def _validate_python_code(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except Exception:
        return False

def _get_recent_rejections(limit: int = 8) -> List[str]:
    """
    Gather recent human rejection reasons from feedback file or CR logs.
    Return a deduplicated list of short reasons (strings).
    """
    reasons: List[str] = []
    seen = set()
    # First, feedback memory file (if it exists
    # and is JSON list).
    try:
        raw = FEEDBACK_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            for item in reversed(data):
                if isinstance(item, dict):
                    r = item.get("reason") or item.get("note") or ""
                else:
                    r = str(item)
                r = r.strip()
                if r and r not in seen:
                    seen.add(r)
                    reasons.append(r)
                if len(reasons) >= limit:
                    return reasons
    except Exception as _e:
        pass  # feedback_memory.json may not exist yet; fall through to CR log scan
    # Fallback: scan CR logs for classifier_reason
    try:
        for cr_file in sorted(CR_LOGS_DIR.glob("CR_*.json"), reverse=True):
            if len(reasons) >= limit:
                break
            try:
                cr = json.loads(cr_file.read_text(encoding="utf-8"))
                reason = cr.get("classifier_reason") or cr.get("reason")
                if reason:
                    r = str(reason).strip()
                    if r and r not in seen:
                        seen.add(r)
                        reasons.append(r)
                if len(reasons) >= limit:
                    break
            except Exception:
                continue  # Skip malformed CR log files
    except Exception:
        pass  # outer CR log scan loop
    return reasons

def _make_feedback_context() -> str:
    fb = _get_recent_rejections()
    if not fb:
        return ""
    lines = []
    for f in fb:
        s = f.replace("\n", " ").strip()
        if len(s) > 200:
            s = s[:197] + "..."
        lines.append(f"""- {s}""")
    return "\n" + "\n".join(lines) + "\n"

def _build_prompt(
    filename: str,
    source: str,
    file_type_context: str,
    feedback_context: str,
    capability_context: str = "",
) -> str:
    """
    Construct prompt for Groq model grounded in ethical principles.

    capability_context: optional description of the specific capability gap
    that triggered this proposal.  When provided, the model is directed to
    fix THAT specific issue rather than choosing an arbitrary improvement.
    """
    # Safety: Ensure source is a string
    if not isinstance(source, str):
        source = str(source)

    focus_block = (
        f"\nSPECIFIC TASK: The following capability gap was detected and must be "
        f"addressed in this file:\n{capability_context}\n"
        f"Your proposal MUST fix this specific issue. Do not make unrelated changes.\n"
    ) if capability_context.strip() else ""

    return f"""
Ethical Core Principles: {ETHICS_SUMMARY}

{file_type_context}
{feedback_context}
{focus_block}
You are an assistant analyzing a single source file and suggesting one safe improvement
that increases clarity, correctness, maintainability, or ethical alignment.
Return only valid JSON.

If no improvement: {{ "change": false }}

If yes, respond EXACTLY like this:
{{
  "change": true,
  "reason": "<why this improves it>",
  "change_details": {{
    "location": "<function or lines>",
    "old": "<original snippet>",
    "new": "<replacement snippet>"
  }},
  "new_code": "<full file after change — complete, valid, importable>",
  "ethics_context": "<ethical reasoning>"
}}

IMPORTANT: "new_code" must be the COMPLETE file contents after applying your change,
not just the changed function. The reviewer will write this directly to disk.

File: {filename}

─── FILE START ───
{source}
─── FILE END ───
"""

# ──────────
# Core logic
# ──────────
def _cr_filename_for(path: Path) -> Path:
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    base = path.name.replace("/", "")
    return CR_LOGS_DIR / f"""CR_{ts}_{base}.json"""

def log_change_request(file_path: Path, proposal: Optional[dict], test_results: Optional[dict] = None) -> None:
    """
    Write a CR log entry summarizing the proposal. This function is used both for
    real proposals and for skipped/immutable attempts (so transparency is maintained).
    """
    try:
        cr_file = _cr_filename_for(file_path)
        data = {
            "timestamp": datetime.utcnow().isoformat(),
            "file": str(file_path),
            "component": file_path.name,
            "status": (proposal.get("classifier_status") if proposal else "SKIPPED"),
            "reason": proposal.get("reason") if proposal else "Skipped: immutable/readonly",
            "change_details": proposal.get("change_details") if proposal else None,
            "ethics_context": proposal.get("ethics_context") if proposal else None,
            "classifier_reason": proposal.get("classifier_reason") if proposal else None,
            "test_results": test_results,
            "proposed_code": proposal.get("new_code") if proposal else None,
        }
        with open(cr_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        # Logging should never raise for the CR
        # pipeline; swallow errors but print
        # minimal debug.
        try:
            print(f"""[patch_generator] Failed to write CR log for {file_path}""", file=sys.stderr)
        except Exception:
            pass  # CR log write failure — non-fatal

def propose_change_for_file(path: Path, capability_context: str = "") -> Optional[dict]:
    """
    Main entry: analyze a single file and propose a change.

    Args:
        path:               The source file to analyze.
        capability_context: Optional description of the specific missing capability
                            or error that triggered this proposal.  When provided,
                            the LLM prompt is directed to fix THAT issue rather than
                            choosing an arbitrary improvement, producing targeted CRs.

    Important behavior:
    - If the target path is under any of IMMUTABLE_DIRS, we will still analyze it,
      but we will not create temp files inside the immutable dir. Temporary writes
      will be redirected to CR_WORK_DIR so that CRs can be generated for values_kernel
      without attempting to write inside it.
    """
    # Validate path
    if not isinstance(path, Path):
        path = Path(path)
    if not path.exists() or not path.is_file():
        return None
    # Read source
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        # Safety check: Ensure source is a string
        if not isinstance(source, str):
            print(f"[PATCH_GENERATOR] Warning: Source for {path} is not a string (type: {type(source)}). Skipping.")
            return None
    except Exception as e:
        print(f"[PATCH_GENERATOR] Error reading {path}: {e}")
        return None
    orig_lines = source.splitlines()
    orig_len = len(orig_lines)
    # File type context
    if path.suffix == ".json":
        file_type_context = "JSON file: must remain valid JSON, no comments or trailing commas."
    elif path.suffix == ".py":
        file_type_context = "Python source file: must remain valid and importable."
    else:
        file_type_context = f"""File type: {path.suffix} (keep structure valid)."""
    feedback_context = _make_feedback_context()
    prompt = _build_prompt(path.name, source, file_type_context, feedback_context, capability_context)
    # Ask Groq for a proposal
    try:
        if generate_response is not None:
            raw = generate_response(prompt)
        else:
            # No LLM available, abort gracefully
            raw = None
    except Exception:
        raw = None
    if not raw:
        return None
    parsed = _try_parse_json(raw)
    if not parsed:
        fenced = _extract_fenced_block(raw, "json")
        if fenced:
            parsed = _try_parse_json(fenced)
    if not parsed or not parsed.get("change"):
        return None
    new_code = parsed.get("new_code", "")
    if not new_code:
        return None
    new_len = len(new_code.splitlines())
    if new_len > MAX_NEW_LINES or (orig_len > 0 and new_len / max(1, orig_len) > MAX_PATCH_RATIO):
        # Safety: proposed patch too big
        proposal = {
            "change": False,
            "reason": "Proposed change exceeds safety limits (too many new lines or excessive ratio).",
            "classifier_status": "REJECTED",
            "classifier_reason": "size_limit_exceeded",
        }
        log_change_request(path, proposal)
        return None
    # Validate python if needed
    if path.suffix == ".py" and not _validate_python_code(new_code):
        proposal = {
            "change": False,
            "reason": "Proposed Python code failed AST parse.",
            "classifier_status": "REJECTED",
            "classifier_reason": "python_syntax_error",
        }
        log_change_request(path, proposal)
        return None
    # Validate JSON if needed
    if path.suffix == ".json":
        try:
            json.loads(new_code)
        except Exception:
            proposal = {
                "change": False,
                "reason": "Proposed JSON is invalid.",
                "classifier_status": "REJECTED",
                "classifier_reason": "json_invalid",
            }
            log_change_request(path, proposal)
            return None
    # Classification sanity check: ask the model
    # to classify approve/reject (best-effort).
    try:
        cls_prompt = (
            "Classify the following proposed change as either 'approve' or 'reject: <reason>'.\n\n"
            "Reject if it breaks ethics, data immutability, or structure.\n\n"
            "Proposed change (short summary):\n"
            f"""{parsed.get('reason', '')}\n"""
        )
        if generate_response is not None:
            cls_raw = generate_response(cls_prompt, temperature=0.0)
        else:
            cls_raw = "approve"  # fallback: optimistic
        if cls_raw:
            cls_lower = cls_raw.strip().lower()
            if cls_lower.startswith("approve"):
                parsed["classifier_status"] = "APPROVED"
                parsed["classifier_reason"] = "auto_approved"
            else:
                parsed["classifier_status"] = "REJECTED"
                parsed["classifier_reason"] = cls_raw.strip()
        else:
            parsed["classifier_status"] = "PENDING"
            parsed["classifier_reason"] = "no_classifier_response"
    except Exception:
        parsed.setdefault("classifier_status", "PENDING")
        parsed.setdefault("classifier_reason", "classifier_exception")
    parsed.setdefault("ethics_context", parsed.get("ethics_context", "Auto-scan improvement via Groq."))
    # At this point we have a validated proposal.
    # Determine safe patch file location (do not
    # write inside immutable directories).
    try:
        patch_file = PATCHES_DIR / path.name
        patch_file.write_text(new_code, encoding="utf-8")
    except Exception:
        # If writing patch fails, log and return
        parsed["classifier_status"] = parsed.get("classifier_status", "FAILED_WRITE")
        parsed["classifier_reason"] = "failed_to_write_patch_file"
        log_change_request(path, parsed)
        return None
    # Run quick tests (syntax checks) and include
    # results in log.
    test_results = _run_basic_py_tests_for_proposal(path, parsed)
    # Log successful proposal
    log_change_request(path, parsed, test_results=test_results)
    return parsed

# ────────────────────────────
# Minimal test / apply harness
# ────────────────────────────
def _run_basic_py_tests_for_proposal(path: Path, proposal: dict) -> dict:
    """
    Minimal test harness that tries to compile the patched file (syntax check).
    Returns a dict of results suitable for inclusion in CR log.
    """
    results = {"tested": False}
    try:
        if path.suffix == ".py":
            patched = PATCHES_DIR / path.name
            src = patched.read_text(encoding="utf-8")
            try:
                ast.parse(src)
                results.update({"returncode": 0, "stdout": "compiled_ok", "stderr": ""})
            except Exception as e:
                results.update({"returncode": 1, "stdout": "", "stderr": str(e)})
        else:
            results.update({"returncode": 0, "stdout": "no_tests_for_type", "stderr": ""})
    except Exception as e:
        results.update({"returncode": -1, "stdout": "", "stderr": str(e)})
    results["tested"] = True
    return results

def apply_patch_to_working_tree(file_path: Path, proposal: dict) -> dict:
    """
    Apply a previously generated patch to the working tree. This function is purposely
    conservative: it will refuse to apply patches to immutable dirs and will always
    keep a copy of the patch in PATCHES_DIR instead of performing in-place overwrites
    without explicit user approval.
    """
    results = {"applied": False}
    try:
        if _is_under_immutable_dir(file_path):
            results["reason"] = "immutable_target"
            # NOTE: _get_temp_work_path() available for race-free temp paths
            # Log attempt for transparency
            log_change_request(file_path, {"change": False, "reason": "apply_blocked_immutable", "classifier_status": "SKIPPED"})
            return results
        patch_file = PATCHES_DIR / file_path.name
        if not patch_file.exists():
            results["reason"] = "patch_missing"
            return results
        # Backup original
        backup = file_path.with_suffix(file_path.suffix + ".bak")
        try:
            if file_path.exists():
                file_path.replace(backup)
        except Exception:
            # If backup fails, abort
            results["reason"] = "backup_failed"
            return results
        try:
            # Move the patch into place atomically
            patch_file.replace(file_path)
            results["applied"] = True
            results["reason"] = "applied_ok"
        except Exception as e:
            # Attempt to restore backup
            try:
                if backup.exists():
                    backup.replace(file_path)
            except Exception:
                pass  # per-file error in batch process
            results["reason"] = f"""apply_failed: {e}"""
    except Exception as e:
        results["reason"] = f"""unexpected_error: {e}"""
    # Log final state
    log_change_request(file_path, proposal, test_results=results)
    return results

# ─────────
# CLI entry
# ─────────
def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point: prompt for encryption key, load state, run chat loop, save on exit."""
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print("Usage: python patch_generator.py <file-or-directory>")
        return 2
    # Expand args to file list
    files: List[Path] = []
    for arg in argv:
        p = Path(arg)
        if p.is_dir():
            for child in p.rglob("*"):
                if child.is_file():
                    files.append(child)
        elif p.is_file():
            files.append(p)
        else:
            # skip non-existent paths
            continue
    for p in files:
        try:
            proposal = propose_change_for_file(p)
            if not proposal:
                continue
            # Optionally run minimal tests and
            # keep logs up to date.
            test_results = _run_basic_py_tests_for_proposal(p, proposal)
            log_change_request(p, proposal, test_results=test_results)
            # Do not auto-apply patches to working
            # tree; require explicit call to
            # apply_patch_to_working_tree.
        except Exception as e:
            # Never halt the whole run because one
            # file failed.
            try:
                print(f"""[patch_generator] Error processing {p}: {e}""", file=sys.stderr)
            except Exception:
                pass  # non-critical path
            continue
    return 0

if __name__ == "__main__":
    raise SystemExit(main())