"""
Contract tests for conversation_engine/memory_engine.py.

Covers the exact bug class found during review: store_exchange was
defined twice with incompatible signatures (Python silently keeps only
the last one), and the surviving definition was being used by
new_main_chat.py to store non-dialogue bootstrap data, which then got
replayed by recall_context() as if it were a real user message.
"""
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from conversation_engine.memory_engine import MemoryEngine


def _isolated_engine():
    scratch = Path(tempfile.mkdtemp(prefix="mem_test_"))
    engine = MemoryEngine(path=str(scratch / "memory_store.json"))
    return engine, scratch


def test_store_exchange_only_defined_once():
    # Guards against the exact bug found: two methods with the same
    # name in the class body, where the second silently shadows the
    # first. If someone reintroduces a duplicate, this won't directly
    # catch it via source inspection (Python doesn't expose that
    # easily) - so instead we pin down the *behavior* of the one
    # definition that actually exists and runs.
    import inspect
    sig = inspect.signature(MemoryEngine.store_exchange)
    params = list(sig.parameters.keys())
    assert params == ["self", "user_id", "user_input", "ai_response"], (
        f"store_exchange signature changed unexpectedly: {params}"
    )


def test_store_exchange_appears_in_recall_context():
    engine, scratch = _isolated_engine()
    try:
        engine.store_exchange("user1", "hello there", "hi, how can I help?")
        context = engine.recall_context("user1")
        assert "hello there" in context
        assert "hi, how can I help?" in context
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_system_note_is_excluded_from_recall_context():
    engine, scratch = _isolated_engine()
    try:
        engine.store_system_note("user1", "SYSTEM_IDENTITY_MODEL", "some internal self-model dump")
        context = engine.recall_context("user1")
        # This is the actual bug: bootstrap/system data should never
        # show up as if the user typed it.
        assert "SYSTEM_IDENTITY_MODEL" not in context
        assert "some internal self-model dump" not in context
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_system_notes_and_real_dialogue_coexist_correctly():
    engine, scratch = _isolated_engine()
    try:
        engine.store_system_note("user1", "SYSTEM_IDENTITY_MODEL", "internal bootstrap data")
        engine.store_exchange("user1", "what's the weather?", "it's sunny today")
        context = engine.recall_context("user1")
        assert "what's the weather?" in context
        assert "it's sunny today" in context
        assert "internal bootstrap data" not in context
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


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
