"""
Tests for agent_trace.py's log rotation, added after noting
agent_trace.jsonl had no size limit at all (unlike plans.log, which
already had rotate_mb/rotate_keep config) and would grow forever.
"""
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent_trace as at


def test_rotation_triggers_above_size_limit():
    scratch_dir = Path("/tmp/agent_trace_rotation_test")
    scratch_dir.mkdir(exist_ok=True)
    trace_path = scratch_dir / "agent_trace.jsonl"
    try:
        trace_path.write_text("x" * 2000)
        at._rotate_trace_if_needed(trace_path, max_mb=0.001, keep=3)
        assert (scratch_dir / "agent_trace.1").exists()
        assert not trace_path.exists()
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def test_rotation_does_not_trigger_below_size_limit():
    scratch_dir = Path("/tmp/agent_trace_rotation_test2")
    scratch_dir.mkdir(exist_ok=True)
    trace_path = scratch_dir / "agent_trace.jsonl"
    try:
        trace_path.write_text("small content")
        at._rotate_trace_if_needed(trace_path, max_mb=5, keep=3)
        assert trace_path.exists()
        assert not (scratch_dir / "agent_trace.1").exists()
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def test_finish_actually_writes_a_trace_entry():
    original_path = at.TRACE_PATH
    scratch_dir = Path("/tmp/agent_trace_write_test")
    scratch_dir.mkdir(exist_ok=True)
    at.TRACE_PATH = scratch_dir / "agent_trace.jsonl"
    try:
        trace = at.TurnTrace("hello")
        trace.finish("a response", outcome="completed")
        assert at.TRACE_PATH.exists()
        entries = at.read_recent_traces(1)
        assert len(entries) == 1
        assert entries[0]["outcome"] == "completed"
    finally:
        at.TRACE_PATH = original_path
        shutil.rmtree(scratch_dir, ignore_errors=True)


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS: {t.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL: {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
