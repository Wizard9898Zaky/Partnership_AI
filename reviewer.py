#!/usr/bin/env python3
"""
reviewer.py  — Interactive Change Request reviewer for Partnership_AI.

Reads CRs from cr_logs/ and lets the human approve, reject, modify, or skip each one.

CR schema (canonical — produced by adaptive_agent._log_cr and patch_generator):

    {
      "change":         true,
      "reason":         "<why this improves it>",
      "change_details": {
        "location": "<function or lines>",
        "old":      "<original snippet>",
        "new":      "<replacement snippet>"
      },
      "new_code":           "<full file after change>",
      "ethics_context":     "<ethical reasoning>",
      -- envelope --
      "timestamp":          "...",
      "user_goal":          "...",
      "missing_capability": "...",
      "reasoning":          "...",
      "file":               "conversation_engine/action_registry.py",
      "patch_written":      true,
      "status":             "PENDING_APPROVAL"
    }

Fixes applied vs previous version:
  - Only PENDING / PENDING_APPROVAL CRs are loaded (not APPROVED/REJECTED noise)
  - print_cr_summary() renders change_details, reason, ethics_context, user_goal
  - Accept path reads new_code from CR directly (no dependency on patches/ existing)
    but falls back to patches/<name> if new_code is absent (old format)
  - Syntax validation (ast.parse) before any .py file is written on Accept
  - Atomic write (tmp → rename) on Accept — never leaves a half-written file
  - 'V' (View) shows the full new_code from the CR itself
  - 'M' (Modify reason) now only modifies the reason field, not silently Accepts
  - Backup (.old) created before overwriting original
  - Rejection reason appended to feedback_memory.json (deduped, capped at 100)
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from values_kernel.invariants import check_invariants, is_kill_switch_active
from smoke_test import run_smoke_test
from app_config import get_config
from rollback import save_versioned_backup

ROOT          = Path(__file__).parent.resolve()
CR_LOGS_DIR   = ROOT / "cr_logs"
PATCHES_DIR   = ROOT / "patches"
FEEDBACK_FILE = ROOT / "feedback_memory.json"

CR_LOGS_DIR.mkdir(exist_ok=True)
PATCHES_DIR.mkdir(exist_ok=True)

if not FEEDBACK_FILE.exists():
    FEEDBACK_FILE.write_text(json.dumps({"rejections": []}, indent=2), encoding="utf-8")


# ═══════════════════════════════
# Helpers
# ═══════════════════════════════

def _append_feedback(reason: str) -> None:
    """Append a rejection reason to feedback_memory.json (deduped, capped at 100)."""
    try:
        data = json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {"rejections": []}
    reasons: list = data.get("rejections", [])
    r = reason.strip()
    if r and r not in reasons:
        reasons.insert(0, r)
        data["rejections"] = reasons[:100]
        FEEDBACK_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("[FEEDBACK] Rejection reason saved to feedback_memory.json")


def _make_executable(path: Path) -> None:
    """chmod +x equivalent."""
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


# ═══════════════════════════════
# Loading
# ═══════════════════════════════

def load_pending_requests() -> list[tuple[Path, dict]]:
    """
    Scan cr_logs/ and return CRs whose status is PENDING or PENDING_APPROVAL only.
    Already-decided CRs (APPROVED, REJECTED, etc.) are intentionally excluded so
    the review queue stays clean across sessions.
    """
    pending = []
    for file in sorted(CR_LOGS_DIR.glob("CR_*.json")):
        try:
            log = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
        status = log.get("status", "")
        if status in ("PENDING", "PENDING_APPROVAL"):
            pending.append((file, log))
    return pending


# ═══════════════════════════════
# Display
# ═══════════════════════════════

def print_cr_summary(file_path: Path, log: dict) -> None:
    """
    Print a human-readable summary of a CR.
    Renders all fields produced by the canonical schema so the reviewer
    has full context before deciding.
    """
    SEP = "═" * 80
    sep = "─" * 80
    print(f"\n{SEP}")
    print(f"  CR FILE : {file_path.name}")
    print(f"  TIME    : {log.get('timestamp', 'unknown')}")
    print(f"  STATUS  : {log.get('status', 'PENDING_APPROVAL')}")
    print(sep)

    # ── Goal context ──────────────────────────────────────────────────────────
    if log.get("user_goal"):
        print(f"  USER GOAL          : {log['user_goal']}")
    if log.get("missing_capability"):
        print(f"  MISSING CAPABILITY : {log['missing_capability']}")
    if log.get("reasoning"):
        print(f"  REASONING          : {log['reasoning']}")
    if log.get("file"):
        print(f"  TARGET FILE        : {log['file']}")
    print()

    # ── Proposal ─────────────────────────────────────────────────────────────
    if not log.get("change", True) is False and not log.get("new_code"):
        print("  ⚠️  No code proposal in this CR — gap was logged without a patch.")
        print(f"{SEP}\n")
        return

    reason = log.get("reason", "")
    if reason:
        print(f"  REASON : {reason}")
        print()

    cd = log.get("change_details", {})
    if cd:
        print("  📍 CHANGE LOCATION :", cd.get("location", "(unknown)"))
        old = cd.get("old", "").strip()
        new = cd.get("new", "").strip()
        if old:
            print("\n  ── OLD ──")
            for line in old.splitlines()[:20]:
                print(f"    {line}")
            if len(old.splitlines()) > 20:
                print(f"    … ({len(old.splitlines()) - 20} more lines)")
        if new:
            print("\n  ── NEW ──")
            for line in new.splitlines()[:20]:
                print(f"    {line}")
            if len(new.splitlines()) > 20:
                print(f"    … ({len(new.splitlines()) - 20} more lines)")
        print()

    ec = log.get("ethics_context", "").strip()
    if ec:
        print(f"  ⚖️  ETHICS : {ec[:300]}")
        print()

    new_code = log.get("new_code", "")
    if new_code:
        lines = new_code.splitlines()
        print(f"  💾 FULL FILE READY : {len(lines)} lines — use (V) to view")

    print(f"{SEP}\n")


# ═══════════════════════════════
# Reviewing
# ═══════════════════════════════

def review_one(file_path: Path, log: dict) -> None:
    """
    Interactive review loop for a single CR.

    Accept  — validates syntax (for .py), writes file atomically, saves backup.
    Reject  — records reason in feedback_memory.json, marks CR rejected.
    Modify  — edit the reason field, then re-prompt for Accept/Reject/Skip.
    View    — print the full proposed new_code.
    Skip    — leave CR in PENDING state, move to next.
    """
    print_cr_summary(file_path, log)

    # ── Resolve target file path ──────────────────────────────────────────────
    target_file_str: str = log.get("file", "")
    orig: Path | None = Path(target_file_str) if target_file_str else None

    def _get_new_code() -> str:
        """Return the full proposed file content, preferring CR's new_code field."""
        nc = log.get("new_code", "").strip()
        if nc:
            return nc
        # Fallback: old-format patches/ file
        if orig:
            patch = PATCHES_DIR / orig.name
            if patch.exists():
                return patch.read_text(encoding="utf-8")
        return ""

    while True:
        decision = input(
            "Decision — (A)ccept  (R)eject  (M)odify reason  (V)iew full code  (S)kip : "
        ).strip().upper()

        # ── View ─────────────────────────────────────────────────────────────
        if decision == "V":
            code = _get_new_code()
            if not code:
                print("\n  [!] No code content available in this CR.\n")
            else:
                lines = code.splitlines()
                print(f"\n{'─'*70}")
                print(f"  Full proposed file  ({len(lines)} lines)")
                print(f"{'─'*70}")
                # Page output through less if available, else print directly
                try:
                    proc = subprocess.run(
                        ["less", "-R"],
                        input=code,
                        text=True,
                        check=False,
                    )
                except FileNotFoundError:
                    # less not available (e.g. Windows / minimal container)
                    page_size = 40
                    for i in range(0, len(lines), page_size):
                        print("\n".join(f"  {l}" for l in lines[i:i+page_size]))
                        if i + page_size < len(lines):
                            cont = input("  -- more -- (Enter to continue, q to stop) : ")
                            if cont.strip().lower() == "q":
                                break
                print(f"{'─'*70}\n")
            continue

        # ── Accept ───────────────────────────────────────────────────────────
        elif decision == "A":
            # Invariant check
            inv_ok, inv_reason = check_invariants(log)
            if not inv_ok:
                print(f"\n  🚫 BLOCKED BY INVARIANT CHECK:\n     {inv_reason}\n")
                log["status"]           = "REJECTED"
                log["decision_time"]    = _utc_now()
                log["rejection_reason"] = f"Auto-rejected by invariant: {inv_reason}"
                _save_cr(file_path, log)
                return

            new_code = _get_new_code()
            if not new_code:
                print("\n  [ERROR] No code to apply — CR has no new_code and no patch file.\n")
                continue

            if not orig:
                print("\n  [ERROR] CR has no 'file' field — cannot determine target path.\n")
                continue

            # ── Syntax validation for Python files ───────────────────────────
            if orig.suffix == ".py":
                try:
                    ast.parse(new_code)
                except SyntaxError as exc:
                    print(f"\n  ❌ SYNTAX ERROR in proposed code — refusing to apply:\n     {exc}\n")
                    print("  Reject this CR and fix the proposal before re-submitting.\n")
                    continue

            # ── Pre-accept smoke test ────────────────────────────────────────
            # Runs the full tests/ suite against a scratch copy of the
            # project with this CR's proposed change overlaid. Syntax
            # validation alone (above) would not have caught, e.g., a
            # change that breaks a different module's contract with the
            # one being edited - the exact bug class found during review
            # (store_exchange's silently-shadowed second definition,
            # check_invariants reading the wrong dict key). Gated by
            # config so it can be turned off in environments without a
            # test suite available.
            if orig.suffix == ".py" and get_config().get("reviewer", {}).get("run_full_test_suite_on_accept", True):
                print("\n  🧪 Running smoke test (full test suite against proposed change)...")
                smoke_ok, smoke_message = run_smoke_test(str(orig), new_code)
                if not smoke_ok:
                    print(f"\n  ❌ SMOKE TEST FAILED — refusing to apply:\n{smoke_message}\n")
                    print("  Reject this CR and fix the proposal before re-submitting.\n")
                    continue
                print(f"  ✓ Smoke test passed. ({smoke_message})\n")

            # ── Backup original (versioned - keeps history, not just the
            #    single most-recently-overwritten .bak) ──────────────────────
            if orig.exists():
                backup = save_versioned_backup(orig)
                if backup:
                    print(f"  [BACKUP] {orig.name} → {backup.relative_to(ROOT) if backup.is_relative_to(ROOT) else backup}")

            # ── Atomic write ─────────────────────────────────────────────────
            try:
                orig.parent.mkdir(parents=True, exist_ok=True)
                tmp = orig.with_suffix(orig.suffix + ".tmp")
                tmp.write_text(new_code, encoding="utf-8")
                os.replace(tmp, orig)
            except Exception as exc:
                print(f"\n  [ERROR] Failed to write file: {exc}\n")
                continue

            if orig.suffix == ".py":
                _make_executable(orig)

            # ── Clean up patch file (if one exists) ───────────────────────────
            if orig:
                patch = PATCHES_DIR / orig.name
                if patch.exists():
                    patch.unlink()

            log["status"]        = "APPROVED"
            log["decision_time"] = _utc_now()
            print(f"\n  ✅ APPLIED → {orig}  ({len(new_code.splitlines())} lines written)\n")
            _save_cr(file_path, log)
            return

        # ── Reject ───────────────────────────────────────────────────────────
        elif decision == "R":
            reason = input("  Reason for rejection (short, for patch_generator feedback): ").strip()
            log["status"]           = "REJECTED"
            log["decision_time"]    = _utc_now()
            log["rejection_reason"] = reason or "No reason given."
            print(f"  [REJECTED] {log['rejection_reason']}")
            if reason:
                _append_feedback(reason)
            # Clean up patch file
            if orig:
                patch = PATCHES_DIR / orig.name
                if patch.exists():
                    patch.unlink()
            _save_cr(file_path, log)
            return

        # ── Modify reason ────────────────────────────────────────────────────
        elif decision == "M":
            current = log.get("reason", "")
            print(f"  Current reason: {current or '(none)'}")
            new_reason = input("  New reason (Enter to keep current): ").strip()
            if new_reason:
                log["reason"] = new_reason
                print(f"  [UPDATED] Reason changed.")
            # Re-print summary and re-prompt — don't auto-accept
            print_cr_summary(file_path, log)
            continue

        # ── Skip ─────────────────────────────────────────────────────────────
        elif decision == "S":
            print("  [SKIPPED]\n")
            return

        else:
            print("  [INVALID] Enter A, R, M, V, or S.\n")
            continue


def _save_cr(file_path: Path, log: dict) -> None:
    """Persist updated CR status back to disk."""
    try:
        file_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
        print(f"  [SAVED] {file_path.name} updated.")
    except Exception as exc:
        print(f"  [ERROR] Failed to save CR file: {exc}")


# ═══════════════════════════════
# Entry point
# ═══════════════════════════════

def main() -> None:
    """Interactive CLI loop: review all pending Change Requests one by one."""
    if is_kill_switch_active():
        print("🛑  Kill switch is active (kill_switch.flag present).")
        print("    Remove the flag file before reviewing CRs: rm kill_switch.flag")
        return

    pending = load_pending_requests()
    if not pending:
        print("✅  No pending Change Requests found.")
        return

    print(f"\n📋  Found {len(pending)} pending Change Request(s).\n")
    for file_path, log in pending:
        review_one(file_path, log)

    print("\n[DONE] All pending CRs reviewed.")


if __name__ == "__main__":
    main()
