#!/usr/bin/env python3
# conversation_engine/self_model.py

"""
SelfModel — Recursive in-memory self-model loader and inspector
────────────────────────────────────────────────────────────────

Purpose
───────
Provides a safe, read-only, in-memory "digital twin" of the code files the AI
is allowed to inspect.

This updated version adds:
- Safe introspective insight extraction
- Ethics-vetted integration with MemoryEngine.store_insight()
- Controlled cross-module communication
"""

from __future__ import annotations
import ast
import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Any
from .memory_engine import MemoryEngine

# ────────────
# Type aliases
# ────────────
Snapshot = Dict[str, str]
IndexMap = Dict[str, Any]
SearchResult = Tuple[str, int, str]

class SelfModel:
    """Snapshot of the agent's self-reported performance and behavioural metrics.

    Populated by AdaptiveAgent._build_self_model() and consumed by the
    self-reflector to detect drift, capability gaps, and ethics violations.
    All fields are optional — missing values are treated as 'not yet measured'.
    """
    def __init__(
        self,
        root: Path | str = "..",
        whitelist_path: Path | str = "introspection_whitelist.json",
        memory_engine: Optional[MemoryEngine] = None,
        intent_engine: Optional[Any] = None,
    ) -> None:
        """
        Initialize the SelfModel.

        Args:
            root: repository root to resolve relative whitelist paths from.
            whitelist_path: path to the JSON whitelist.
            memory_engine: optional MemoryEngine for storing introspective insights.
        """
        self.root = Path(root).resolve()
        self.whitelist_path = (self.root / whitelist_path).resolve()
        self.whitelist: List[str] = []
        self.snapshot: Snapshot = {}
        self.index: IndexMap = {}
        self._human_callback: Optional[Callable[[str, str], str]] = None
        self.intent_engine = intent_engine
        # optional memory integration
        self.memory_engine = memory_engine
        from conversation_engine.capability_providers import (
            filesystem_capabilities,
            memory_capabilities,
            change_request_capabilities,
            collect_capabilities,
        )
        self.capabilities = collect_capabilities([
            lambda: filesystem_capabilities(self.root),
            lambda: memory_capabilities(memory_engine),
            lambda: change_request_capabilities(self.intent_engine),
        ])

    def has_capability(self, capability_name: str) -> bool:
        """
        Check if a specific capability is available.
        
        Args:
            capability_name: Dot-separated path (e.g., "system.introspect")
        
        Returns:
            True if capability exists and is enabled, False otherwise.
        """
        if not hasattr(self, 'capabilities'):
            return False
            
        # Navigate nested dict (e.g., "system.introspect" -> capabilities["system"]["introspect"])
        parts = capability_name.split('.')
        current = self.capabilities
        
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return False
        
        # Return True if the final value is truthy (True, "yes", 1, etc.)
        return bool(current)

    def files_related_to(self, category: str) -> List[str]:
        """
        Return list of files related to a specific category.
        """
        if category == "memory":
            return [f for f in self.whitelist if "memory" in f.lower()]
        if category == "intent":
            return [f for f in self.whitelist if "intent" in f.lower()]
        if category == "dialogue":
            return [f for f in self.whitelist if "dialogue" in f.lower()]
        return self.whitelist  # Fallback: return all whitelisted files

    # ─────────────────
    # Whitelist loading
    # ─────────────────
    def load_whitelist(self) -> List[str]:
        """Load the introspection whitelist; return empty list on failure."""
        if not self.whitelist_path.exists():
            return []
        try:
            raw = self.whitelist_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and "allow_introspection" in data:
                items = list(data.get("allow_introspection", []))
            elif isinstance(data, list):
                items = list(data)
            else:
                items = []
            items = [str(p).strip() for p in items if isinstance(p, (str,))]
            self.whitelist = items
            return items
        except Exception:
            self.whitelist = []
            return []

    # ─────────────────
    # Snapshot building
    # ─────────────────
    def _read_file_safe(self, relpath: str) -> str:
        try:
            p = Path(relpath)
            if not p.is_absolute():
                p = self.root / relpath
            if p.exists() and p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
            else:
                return f"<ERROR: not found: {relpath}>"
        except Exception as e:
            return f"<ERROR: failed to load {relpath}: {e}>"

    def build_snapshot(self) -> Snapshot:
        """Build an in-memory dict snapshot of whitelisted file contents."""
        snapshot: Snapshot = {}
        for rel in sorted(self.whitelist):
            snapshot[str(rel)] = self._read_file_safe(rel)
        self.snapshot = snapshot
        return snapshot

    # ────────────────────────────
    # AST indexer for Python files
    # ────────────────────────────
    @staticmethod
    def _index_python_source(source_text: str) -> dict:
        functions = []
        classes = []
        imports = []
        try:
            tree = ast.parse(source_text)
        except Exception:
            return {"functions": [], "classes": [], "imports": []}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                functions.append({
                    "name": node.name,
                    "lineno": getattr(node, "lineno", None),
                    "end_lineno": getattr(node, "end_lineno", None),
                    "doc": ast.get_docstring(node) or "",
                })
            elif isinstance(node, ast.AsyncFunctionDef):
                functions.append({
                    "name": node.name,
                    "lineno": getattr(node, "lineno", None),
                    "end_lineno": getattr(node, "end_lineno", None),
                    "doc": ast.get_docstring(node) or "",
                    "async": True,
                })
            elif isinstance(node, ast.ClassDef):
                classes.append({
                    "name": node.name,
                    "lineno": getattr(node, "lineno", None),
                    "end_lineno": getattr(node, "end_lineno", None),
                    "doc": ast.get_docstring(node) or "",
                })
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                try:
                    imports.append(ast.get_source_segment(source_text, node) or "")
                except Exception:
                    imports.append("")
        return {"functions": functions, "classes": classes, "imports": imports}

    def build_index(self) -> IndexMap:
        """Build a searchable index of function/class definitions in whitelisted files."""
        idx: IndexMap = {}
        for rel, txt in self.snapshot.items():
            if isinstance(rel, str) and rel.endswith(".py"):
                try:
                    idx[rel] = self._index_python_source(txt)
                except Exception:
                    idx[rel] = {"functions": [], "classes": [], "imports": []}
        self.index = idx
        return idx

    # ──────────────────────────
    # Mapping / dict-like access
    # ──────────────────────────
    def items(self):
        """Yield (path, content) pairs from the file snapshot."""
        return self.snapshot.items()

    def keys(self):
        """Yield file paths in the snapshot."""
        return self.snapshot.keys()

    def values(self):
        """Yield file contents in the snapshot."""
        return self.snapshot.values()

    def __iter__(self):
        return iter(self.snapshot)

    def __getitem__(self, key: str) -> str:
        return self.snapshot[key]

    def get_snapshot(self) -> Snapshot:
        """Return the full file snapshot dict."""
        return self._immutable_snapshot_copy()

    # ────────────────────
    # Public load / reload
    # ────────────────────
    def load(self) -> None:
        """Refresh the snapshot and index from disk."""
        self.load_whitelist()
        self.build_snapshot()
        self.build_index()

    def reload(self) -> None:
        """Alias for load() — refresh snapshot and index."""
        self.load()

    # ─────────────────────
    # Introspection helpers
    # ─────────────────────
    def list_files(self) -> List[str]:
        """Return a sorted list of all files in the snapshot."""
        return list(self.snapshot.keys())

    def get_file(self, key_or_name: str) -> str:
        """Return the content of a specific file, or None if not in snapshot."""
        if key_or_name in self.snapshot:
            return self.snapshot[key_or_name]
        name = Path(key_or_name).name
        matches = [v for k, v in self.snapshot.items() if Path(k).name == name]
        if matches:
            return matches[0]
        return "<Access denied or file not in whitelist>"

    def get_file_lines(self, key_or_name: str) -> List[str]:
        """Return the content of a file split into lines."""
        content = self.get_file(key_or_name)
        if content.startswith("<ERROR:") or content == "<Access denied or file not in whitelist>":
            return []
        return content.splitlines()

    def snippet(self, key_or_name: str, lineno: int, context: int = 3) -> str:
        """Return a slice of lines from a file (start_line to end_line, 1-indexed)."""
        lines = self.get_file_lines(key_or_name)
        if not lines:
            return ""
        idx = max(0, lineno - 1)
        start = max(0, idx - context)
        end = min(len(lines), idx + context + 1)
        numbered = []
        for i in range(start, end):
            prefix = "-> " if i == idx else "   "
            numbered.append(f"{prefix}{i+1:4d}: {lines[i]}")
        return "\n".join(numbered)

    def get_index(self, key_or_name: str) -> dict:
        """Return the full symbol index dict."""
        if key_or_name in self.index:
            return self.index[key_or_name]
        name = Path(key_or_name).name
        for k, v in self.index.items():
            if Path(k).name == name:
                return v
        return {"functions": [], "classes": [], "imports": []}

    # ───────────────────────────
    # Search (substring or regex)
    # ───────────────────────────
    def search(self, query: str, regex: bool = False, max_results: int = 50) -> List[SearchResult]:
        """Search the snapshot for files whose content contains the query string."""
        results: List[SearchResult] = []
        if not query:
            return results
        pattern = None
        if regex:
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error:
                pattern = None
        for fname, text in self.snapshot.items():
            if text.startswith("<ERROR:"):
                continue
            lines = text.splitlines()
            for i, line in enumerate(lines, start=1):
                matched = False
                if pattern:
                    if pattern.search(line):
                        matched = True
                else:
                    if query.lower() in line.lower():
                        matched = True
                if matched:
                    snippet = line.strip()
                    results.append((fname, i, snippet))
                    if len(results) >= max_results:
                        return results
        return results

    # ───────────────────
    # Summaries & helpers
    # ───────────────────
    def summary(self) -> Dict[str, Any]:
        """Return a human-readable summary string of the model's state."""
        return {
            "root": str(self.root),
            "whitelist_count": len(self.whitelist),
            "files": {k: (len(v) if isinstance(v, str) else 0) for k, v in self.snapshot.items()},
        }

    def short_manifest(self) -> List[str]:
        """Return a compact one-line-per-file manifest of the snapshot."""
        return list(self.snapshot.keys())

    # ──────────────────────
    # Human-question gateway
    # ──────────────────────
    def attach_human_callback(self, callback: Callable[[str, str], str]) -> None:
        """Register a callable that the AI can use to ask the human a question."""
        self._human_callback = callback

    def ask_human(self, question: str, prompt: str = "Answer") -> str:
        """Invoke the registered human callback with a question; return the answer."""
        if not self._human_callback:
            raise RuntimeError("No human callback attached. Attach with attach_human_callback()")
        return self._human_callback(question, prompt)

    # ────────────────────────────────
    # Insight Extraction (NEW FEATURE)
    # ────────────────────────────────
    def extract_insights(self) -> List[dict]:
        """
        Generate introspective insights from the snapshot and AST index.
        Outputs a list of dicts suitable for sending to MemoryEngine.store_insight().
        """
        insights = []
        for fname, idx in self.index.items():
            fn_count = len(idx.get("functions", []))
            cls_count = len(idx.get("classes", []))
            imp_count = len(idx.get("imports", []))
            # Insight: file metadata
            insights.append({
                "insight_type": "file_structure",
                "content": f"{fname} contains {fn_count} functions, {cls_count} classes, {imp_count} imports.",
                "source": fname,
            })
            # Insight: notable functions
            for fn in idx.get("functions", []):
                if fn.get("doc"):
                    insights.append({
                        "insight_type": "function_documentation",
                        "content": f"Function {fn['name']} has docstring: {fn['doc'][:200]}",
                        "source": fname,
                    })
        return insights

    # ───────────────────────────────
    # Push Insights into MemoryEngine
    # ───────────────────────────────
    def push_insights_to_memory(self, user_id: str) -> List[dict]:
        """
        Extracts insights and stores each in the user's short-term memory
        through MemoryEngine.store_insight().

        Returns a list of successfully stored insight dicts.
        """
        if not self.memory_engine:
            return []
        insights = self.extract_insights()
        stored = []
        for ins in insights:
            accepted = self.memory_engine.store_insight(
                user_id=user_id,
                insight_type=ins["insight_type"],
                content=ins["content"],
                source_utterance=ins["source"]
            )
            if accepted:
                stored.append(ins)
        return stored

    # ───────────────────────────
    # Prevent accidental mutation
    # ───────────────────────────
    def _immutable_snapshot_copy(self) -> Snapshot:
        return {k: (v[:] if isinstance(v, str) else v) for k, v in self.snapshot.items()}

    # ──────────────
    # Representation
    # ──────────────
    def __repr__(self) -> str:
        return f"<SelfModel root={self.root} files={len(self.snapshot)} whitelist={len(self.whitelist)}>"

# ──────────────────────────
# Module convenience factory
# ──────────────────────────
def build_self_model(
    root: Path | str,
    whitelist: Path | str,
    memory_engine: Optional[MemoryEngine] = None,
    intent_engine: Optional[Any] = None,
) -> SelfModel:
    """
    Explicit factory for constructing a SelfModel with optional memory integration.
    This is the preferred entry point for system wiring.
    """
    sm = SelfModel(
        root=root,
        whitelist_path=whitelist,
        memory_engine=memory_engine,
        intent_engine=intent_engine,
    )
    sm.load()
    return sm
_default_instance: Optional[SelfModel] = None

def get_default_self_model(root: Path | str = "..", whitelist_path: Path | str = "introspection_whitelist.json") -> SelfModel:
    """Build and return a SelfModel using default project-root settings."""
    global _default_instance
    if _default_instance is None:
        _default_instance = SelfModel(root=root, whitelist_path=whitelist_path)
        _default_instance.load()
    return _default_instance

# Self-test when run directly
if __name__ == "__main__":
    sm = get_default_self_model()
    print("SelfModel summary:", json.dumps(sm.summary(), indent=2))
    files = sm.short_manifest()
    if files:
        print("First file (truncated):")
        first = files[0]
        print(sm.get_file(first)[:1000])
    else:
        print("No whitelisted files found. Edit introspection_whitelist.json to add files.")
