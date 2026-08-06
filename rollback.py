#!/usr/bin/env python3
# rollback.py
"""
Versioned backups and rollback for self-applied Change Requests.

Previously, reviewer.py's Accept path made exactly one backup
(`file.py.bak`) before overwriting a target file, and each subsequent
approved change overwrote that same .bak - so only the single
immediately-preceding version could ever be recovered, and there was
no command to actually restore it (a human had to know to manually
`cp file.py.bak file.py`).

This module keeps a timestamped backup history per file (bounded by
config's reviewer.backup_history_limit, oldest pruned first) and
provides a rollback function usable from a CLI command.
"""
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from app_config import get_config

ROOT = Path(__file__).parent.resolve()
BACKUP_ROOT = ROOT / "cr_logs" / "backups"


def _backup_dir_for(rel_path: str) -> Path:
    # Mirror the file's relative *parent* directory structure under
    # cr_logs/backups/ so files with the same basename in different
    # subdirectories (e.g. two __init__.py files) don't collide.
    # Using rel_path directly creates a directory named after the file
    # (e.g. "utils.py/") which Python tries to import as a module.
    d = BACKUP_ROOT / Path(rel_path).parent
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_versioned_backup(orig: Path) -> Optional[Path]:
    """
    Save a timestamped copy of `orig` (given as an absolute path)
    before it gets overwritten, and prune old backups beyond the
    configured history limit. Returns the backup path, or None if
    orig doesn't exist yet (nothing to back up on a brand-new file).
    """
    if not orig.exists():
        return None
    try:
        rel_path = str(orig.resolve().relative_to(ROOT))
    except ValueError:
        rel_path = orig.name  # fallback if orig is outside ROOT for some reason

    backup_dir = _backup_dir_for(rel_path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"{orig.name}.{timestamp}.bak"
    shutil.copy2(str(orig), str(backup_path))

    # Prune beyond the configured limit, oldest first.
    limit = get_config().get("reviewer", {}).get("backup_history_limit", 20)
    existing = sorted(backup_dir.glob(f"{orig.name}.*.bak"))
    while len(existing) > limit:
        oldest = existing.pop(0)
        oldest.unlink(missing_ok=True)

    return backup_path


def list_backups(rel_path: str) -> List[Path]:
    """List available backups for a project-relative file path, oldest first."""
    orig = ROOT / rel_path
    backup_dir = BACKUP_ROOT / Path(rel_path).parent
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob(f"{orig.name}.*.bak"))


def rollback_file(rel_path: str, version_index: int = -1) -> Tuple[bool, str]:
    """
    Restore `rel_path` (project-relative, e.g. "sandbox_executor.py" or
    "conversation_engine/memory_engine.py") from a saved backup.

    version_index: -1 = most recent backup (default), -2 = one before
    that, etc. Positive indices count from the oldest.

    The rollback itself is applied through the same atomic-write
    pattern as reviewer.py, and the current (pre-rollback) state is
    itself backed up first, so a rollback can always be undone by
    rolling back again.
    """
    from values_kernel.invariants import check_invariants, is_kill_switch_active

    # FIX: honour kill switch — no disk writes allowed while halted
    if is_kill_switch_active():
        return False, "Refusing rollback: kill_switch.flag is active. Remove it first."

    ok, reason = check_invariants({"file": rel_path, "new_code": ""})
    if not ok:
        return False, f"Refusing to roll back an immutable-protected path: {reason}"

    backups = list_backups(rel_path)
    if not backups:
        return False, f"No backups found for '{rel_path}'."
    try:
        chosen = backups[version_index]
    except IndexError:
        return False, f"No backup at index {version_index} (have {len(backups)} available)."

    # FIX: enforce path stays inside ROOT (prevent traversal via crafted rel_path)
    target = (ROOT / rel_path).resolve()
    if not str(target).startswith(str(ROOT.resolve())):
        return False, f"Path escape detected: '{rel_path}' resolves outside project root."
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)

    # Back up current state before overwriting, so the rollback itself
    # is reversible.
    save_versioned_backup(target)

    tmp = target.with_suffix(target.suffix + ".rollback.tmp")
    shutil.copy2(str(chosen), str(tmp))
    tmp.replace(target)

    return True, f"Restored '{rel_path}' from backup {chosen.name}."


def main():
    """CLI entry-point for the rollback utility.

    Usage::

        python rollback.py <file>             # roll back to most recent backup
        python rollback.py <file> --list      # list available backups
        python rollback.py <file> --index -2  # use second-most-recent backup
    """
    import argparse
    parser = argparse.ArgumentParser(description="Roll back a Partnership_AI source file to a previous backup.")
    parser.add_argument("file", help="Project-relative path, e.g. sandbox_executor.py")
    parser.add_argument("--list", action="store_true", help="List available backups instead of rolling back")
    parser.add_argument("--index", type=int, default=-1, help="Backup index (-1 = most recent, default)")
    args = parser.parse_args()

    if args.list:
        backups = list_backups(args.file)
        if not backups:
            print(f"No backups found for '{args.file}'.")
            return
        for i, b in enumerate(backups):
            print(f"  [{i}] {b.name}")
        return

    ok, message = rollback_file(args.file, version_index=args.index)
    print(("✓ " if ok else "✗ ") + message)


if __name__ == "__main__":
    main()
