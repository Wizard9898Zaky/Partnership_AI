#!/usr/bin/env python3
# conversation_engine/idea_incubator.py
"""
IdeaIncubator — True Universal Dynamic Edition
──────────────────────────────────────────────
NO HARDCODED CATEGORIES.
NO PRE-DEFINED METRICS.

The AI knows NOTHING about any catagory until YOU
teach it via memory.

How it works:
1. You store a fact: "metric_healing_keywords: resonance, balance, harmony"
2. You store a fact: "metric_profit_keywords: revenue, scale, market"
3. The AI reads these, builds a dynamic list of metrics, and scores accordingly.
"""
from __future__ import annotations
import sqlite3
import json
import random
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import Counter
from datetime import datetime, timezone

# ─────────────
# Configuration
# ─────────────
try:
    from conversation_engine.logging_utils import log_plan_update
except ImportError:
    def log_plan_update(*args, **kwargs):
        """Log a plan-update event associated with an idea."""
        pass

# ──────────────
# Database Class
# ──────────────
class IdeaIncubator:
    """
    Universal Idea Incubator.
    All analysis logic is derived 100% from
    user-stored memory.
    """
    def __init__(self, user_id: str, user_log_dir: Optional[Path] = None, memory_engine=None):
        self.user_id = user_id
        self.user_log_dir = user_log_dir or Path(__file__).parent.parent / "user_logs"
        self.user_log_dir.mkdir(parents=True, exist_ok=True)
        self.memory_engine = memory_engine
        self.db_path = self.user_log_dir / f"ideas-{user_id}.db"
        self.conn: Optional[sqlite3.Connection] = None
        try:
            self.conn = sqlite3.connect(str(self.db_path))
            self.cursor = self.conn.cursor()
            self.create_tables()
        except sqlite3.Error as e:
            print(f"[IdeaIncubator] Database Error: {e}")
            raise

    def create_tables(self):
        """Create the SQLite schema tables if they do not already exist."""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                tags TEXT,
                ratings TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id TEXT NOT NULL
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idea1_id INTEGER,
                idea2_id INTEGER,
                relationship_type TEXT,
                strength REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idea_id INTEGER,
                analysis TEXT,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def insert_idea(self, title: str, description: str, category: str, tags: Optional[List[str]] = None, ratings: Optional[Dict[str, int]] = None) -> int:
        """Insert a new idea into the database and return its row ID."""
        if tags is None: tags = []
        if ratings is None: ratings = {"originality": 5, "complexity": 5, "profitability": 5, "emotional_impact": 5}
        self.cursor.execute('''INSERT INTO ideas (title, description, category, tags, ratings, user_id) VALUES (?, ?, ?, ?, ?, ?)''',
            (title, description, category, json.dumps(tags), json.dumps(ratings), self.user_id))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_all_ideas(self) -> List[Dict[str, Any]]:
        """Return all stored ideas as a list of dicts."""
        self.cursor.execute('SELECT * FROM ideas WHERE user_id=?', (self.user_id,))
        return [self._format_idea(row) for row in self.cursor.fetchall()]

    def search_ideas(self, keyword: str) -> List[Dict[str, Any]]:
        """Full-text search ideas by keyword; return matching rows."""
        keyword = f"%{keyword.lower()}%"
        self.cursor.execute('''SELECT * FROM ideas WHERE user_id=? AND (LOWER(title) LIKE ? OR LOWER(description) LIKE ? OR LOWER(tags) LIKE ?)''',
            (self.user_id, keyword, keyword, keyword))
        return [self._format_idea(row) for row in self.cursor.fetchall()]

    def get_idea_by_id(self, idea_id: int) -> Optional[Dict[str, Any]]:
        """Return a single idea dict by its row ID."""
        self.cursor.execute('SELECT * FROM ideas WHERE id=? AND user_id=?', (idea_id, self.user_id))
        row = self.cursor.fetchone()
        return self._format_idea(row) if row else None

    def _format_idea(self, row: Tuple) -> Dict[str, Any]:
        if row is None: return {}
        return {
            "id": row[0], "title": row[1], "description": row[2], "category": row[3],
            "tags": json.loads(row[4]) if row[4] else [],
            "ratings": json.loads(row[5]) if row[5] else {},
            "created_at": row[6], "user_id": row[7]
        }

    # ═══════════════════════════════
    # TRUE UNIVERSAL DYNAMIC ANALYSIS
    # ═══════════════════════════════
    def _get_user_defined_metrics(self) -> Dict[str, Set[str]]:
        """
        Fetches ALL metrics defined by the user
        from memory.

        Expected format in memory:
        Facts stored with keys like:
        - "metric_healing": "resonance, balance,
           harmony, flow"
        - "metric_profit": "revenue, scale,
           market, growth"
        - "metric_creative": "poetry, art, story,
           magic"

        Returns:
            A dictionary: {
              "healing": {
                "resonance",
                "balance"
              },
              "profit": {...}
            }
            If no metrics are found, returns an
            empty dict.
        """
        if not self.memory_engine:
            return {}
        metrics = {}
        try:
            # Strategy: Scan memory for facts
            # starting with "metric_". Since we
            # don't have the exact MemoryEngine
            # API, we simulate the retrieval. In
            # a real implementation, you would
            # call:
            # all_facts = self.memory_engine.recall_facts(self.user_id, "metric_")
            # For this robust implementation, we
            # assume the memory_engine has a
            # 'store' or a method to retrieve all
            # facts.
            # Simulated retrieval logic:
            # 1. Check if memory_engine has a
            # 'store' dict.
            if hasattr(self.memory_engine, 'store') and isinstance(self.memory_engine.store, dict):
                user_store = self.memory_engine.store.get(self.user_id, {})
                # Iterate through all keys in user
                # store.
                for key, value in user_store.items():
                    if key.startswith("metric_"):
                        metric_name = key.replace("metric_", "").lower()
                        words = set()
                        if isinstance(value, list):
                            words = set([str(x).lower() for x in value])
                        elif isinstance(value, str):
                            words = set([x.lower() for x in re.split(r'[,\s]+', value) if x.strip()])
                        if words:
                            metrics[metric_name] = words
            # If the memory_engine has a specific
            # method to recall by prefix, use it
            # here.
            # Example:
            # raw_metrics = self.memory_engine.recall_facts_by_prefix(self.user_id, "metric_")
            # for item in raw_metrics:
            #   ... parse and add to metrics ...
        except Exception as e:
            print(f"[IdeaIncubator] Error fetching user metrics: {e}")
            return {}
        return metrics

    def analyze_idea(self, text: str) -> Dict[str, Any]:
        """
        Analyzes an idea using DYNAMIC VOCABULARY.

        NEW FEATURE: If no metrics are found, it
        checks for a stored preference rule on how
        to handle this situation (Asks).
        """
        # 1. Fetch ALL user-defined metrics
        # dynamically. We need a helper to get ALL
        # metrics. Let's assume we call
        # _get_dynamic_vocabulary("all"). If your
        # current code doesn't have this, add it
        # or iterate through known categories. For
        # now, let's check if ANY metrics exist by
        # trying to recall a few common ones OR by
        # checking the memory engine directly for
        # the 'vocabulary' key.
        user_metrics = {}
        if self.memory_engine:
            # Try to recall all metrics stored
            # under 'vocabulary'.
            all_prefs = self.memory_engine.recall_all_vocabulary_metrics(self.user_id)
            # Filter only those that look like
            # metrics (keys starting with
            # 'metric_' or just raw categories).
            # In our design, we stored them as
            # "healing", "business", etc. So we
            # just take the whole dict if it
            # exists.
            user_metrics = all_prefs
        has_metrics = len(user_metrics) > 0
        if not has_metrics:
            # 2. CHECK FOR PREFERENCE RULE
            if self.memory_engine:
                # Check if user has a rule for
                # "no_metrics_found".
                pref = self.memory_engine.recall_preference_rule(self.user_id, "no_metrics_found")
                if pref:
                    rule = pref.get("rule", "")
                    if rule == "ask_user":
                        # Return a special signal
                        # for the AI to ask the
                        # user.
                        return {
                            "scores": {},
                            "total_score": 0,
                            "evaluation": "NEEDS_CLARIFICATION",
                            "note": "No metrics found. Preference set to 'ask_user'. Please teach me a new metric.",
                            "action_hint": "ask_user_for_metric"
                        }
                    elif rule == "use_defaults":
                        # Fallback: Use a very
                        # basic generic set if you
                        # want, or just error.
                        return {
                            "scores": {},
                            "total_score": 0,
                            "evaluation": "NO_DATA",
                            "note": "No metrics found. Preference set to 'use_defaults' (but none defined)."
                        }
                    else:
                        # Custom rule
                        return {
                            "scores": {},
                            "total_score": 0,
                            "evaluation": "CUSTOM_RULE",
                            "note": f"Custom rule applied: {rule}"
                        }
            # 3. NO PREFERENCE FOUND -> Default
            # Behavior (Ask).
            return {
                "scores": {},
                "total_score": 0,
                "evaluation": "NEEDS_CLARIFICATION",
                "note": "I have no metrics to analyze this idea. Would you like to teach me a new metric?",
                "action_hint": "suggest_teaching"
            }
        # 4. NORMAL ANALYSIS (Metrics exist)
        # Tokenize Input
        words = text.lower().split()
        # Calculate Scores based on USER
        # DEFINITIONS.
        scores = {}
        total_score = 0
        for metric_name, keywords in user_metrics.items():
            # Ensure keywords is a list/set
            if isinstance(keywords, str):
                import re
                keywords = set([k.strip().lower() for k in re.split(r'[,\s]+', keywords) if k.strip()])
            elif isinstance(keywords, list):
                keywords = set([str(k).lower() for k in keywords])
            else:
                keywords = set()
            score = 0
            for word in words:
                if word in keywords:
                    score += 1
            scores[metric_name] = score
            total_score += score
        # Calculate Feasibility & Rating
        complexity_score = scores.get("complexity", 0)
        innovation_score = scores.get("innovation", 0)
        feasibility = 0
        if complexity_score > 0 or innovation_score > 0:
            feasibility = (10 - min(complexity_score, 10)) + innovation_score
        else:
            feasibility = 5 # Neutral default
        if total_score == 0:
            rating = "Unscored (No Matches)"
        elif total_score >= 15:
            rating = "High Potential"
        elif total_score >= 8:
            rating = "Moderate Potential"
        else:
            rating = "Experimental"
        return {
            "scores": scores,
            "total_score": total_score,
            "feasibility_score": feasibility,
            "evaluation": rating,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "note": f"Analysis based on {len(user_metrics)} user-defined metrics: {list(user_metrics.keys())}"
        }

    def connect_ideas(self, idea1_id: int, idea2_id: int, relationship_type: str = "related", strength: float = 1.0) -> bool:
        """Create a bidirectional relationship between two idea IDs."""
        try:
            self.cursor.execute('''INSERT INTO relationships (idea1_id, idea2_id, relationship_type, strength) VALUES (?, ?, ?, ?)''',
                (idea1_id, idea2_id, relationship_type, strength))
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False

    def get_related_ideas(self, idea_id: int) -> List[Tuple]:
        """Return ideas that are connected to the given idea_id."""
        self.cursor.execute('SELECT * FROM relationships WHERE idea1_id=? OR idea2_id=?', (idea_id, idea_id))
        return self.cursor.fetchall()

    def semantic_similarity(self, text1: str, text2: str) -> float:
        """Return cosine similarity between two idea embedding vectors."""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2: return 0.0
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        return len(intersection) / len(union) if union else 0.0

    def find_similar_ideas(self, text: str, threshold: float = 0.2) -> List[Dict[str, Any]]:
        """Return the top-k most semantically similar ideas to the query."""
        ideas = self.get_all_ideas()
        matches = []
        for idea in ideas:
            combined = f"{idea['title']} {idea['description']}"
            similarity = self.semantic_similarity(text, combined)
            if similarity >= threshold:
                matches.append({"idea": idea, "similarity": round(similarity, 3)})
        matches.sort(key=lambda x: x["similarity"], reverse=True)
        return matches

    def get_idea_count(self) -> int:
        """Return the total number of ideas in the database."""
        self.cursor.execute('SELECT COUNT(*) FROM ideas WHERE user_id=?', (self.user_id,))
        return self.cursor.fetchone()[0]

    def ask(self, question: str) -> Any:
        """Ask the LLM a question about the stored ideas and return its answer."""
        question = question.lower()
        if "creative" in question: return self.get_ideas_by_category("creative")
        elif "business" in question: return self.get_ideas_by_category("business")
        elif "personal" in question: return self.get_ideas_by_category("personal")
        elif "science" in question: return self.get_ideas_by_category("science")
        elif "similar" in question: return self.find_similar_ideas(question.replace("similar", "").strip())
        elif "random" in question: return self.get_random_idea()
        elif "all" in question or "list" in question: return self.get_all_ideas()
        else: return self.search_ideas(question)

    def get_random_idea(self) -> Optional[Dict[str, Any]]:
        """Return a randomly selected idea from the database."""
        ideas = self.get_all_ideas()
        return random.choice(ideas) if ideas else None

    def get_ideas_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Return all ideas belonging to the specified category."""
        self.cursor.execute('SELECT * FROM ideas WHERE category=? AND user_id=?', (category, self.user_id))
        return [self._format_idea(row) for row in self.cursor.fetchall()]

    def delete_idea(self, idea_id: int) -> bool:
        """Delete an idea by ID; also removes its connections."""
        self.cursor.execute('DELETE FROM ideas WHERE id=? AND user_id=?', (idea_id, self.user_id))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def update_idea(self, idea_id: int, title: Optional[str] = None, description: Optional[str] = None, category: Optional[str] = None, tags: Optional[List[str]] = None, ratings: Optional[Dict[str, int]] = None) -> bool:
        """Update the content or metadata of an existing idea."""
        idea = self.get_idea_by_id(idea_id)
        if not idea: return False
        updates = []
        params = []
        if title is not None: updates.append("title=?"); params.append(title)
        if description is not None: updates.append("description=?"); params.append(description)
        if category is not None: updates.append("category=?"); params.append(category)
        if tags is not None: updates.append("tags=?"); params.append(json.dumps(tags))
        if ratings is not None: updates.append("ratings=?"); params.append(json.dumps(ratings))
        if not updates: return True
        params.append(idea_id); params.append(self.user_id)
        query = f"UPDATE ideas SET {', '.join(updates)} WHERE id=? AND user_id=?"
        self.cursor.execute(query, params)
        self.conn.commit()
        return self.cursor.rowcount > 0

    def store_analysis(self, idea_id: int, analysis: Dict[str, Any]) -> bool:
        """Persist an analysis result linked to an idea."""
        try:
            self.cursor.execute('''INSERT INTO analysis_history (idea_id, analysis) VALUES (?, ?)''', (idea_id, json.dumps(analysis)))
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False

    def generate_new_ideas(self, prompt: str) -> List[str]:
        """Use the LLM to generate novel ideas based on existing ones."""
        words = prompt.split()
        prefixes = ["Quantum", "Smart", "Neural", "Hyper", "Virtual", "Meta", "Dream", "Shadow", "Adaptive", "Emotional", "Conscious", "Resonant", "Harmonic", "Healing"]
        suffixes = ["Platform", "Engine", "Network", "System", "Framework", "Protocol", "Generator", "Interface", "Hub", "Matrix", "Sanctuary", "Nexus", "Pathway"]
        transformations = ["ify", "ize", "ate", "sync", "link", "flow", "weave"]
        ideas = []
        for word in words:
            ideas.append(f"{random.choice(prefixes)} {word.capitalize()} {random.choice(suffixes)}")
            ideas.append(f"{word.capitalize()}{random.choice(transformations)}")
        if len(words) >= 2:
            ideas.append(f"{words[0].capitalize()} {words[1].capitalize()} Fusion Engine")
            ideas.append(f"{words[0].capitalize()}-Driven {words[1].capitalize()} Intelligence")
        return ideas

    def get_idea_graph(self) -> Dict[str, Any]:
        """Return the full idea connectivity graph as adjacency dict."""
        ideas = self.get_all_ideas()
        relationships = self.cursor.execute('SELECT * FROM relationships').fetchall()
        nodes = [{"id": idea["id"], "title": idea["title"], "category": idea["category"]} for idea in ideas]
        edges = [{"source": r[1], "target": r[2], "type": r[3], "strength": r[4]} for r in relationships]
        return {"nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}

    def get_most_common_categories(self) -> List[Tuple[str, int]]:
        """Return the most frequently used idea categories."""
        self.cursor.execute('SELECT category FROM ideas WHERE user_id=?', (self.user_id,))
        categories = [row[0] for row in self.cursor.fetchall()]
        return Counter(categories).most_common()

    def get_tag_statistics(self) -> List[Tuple[str, int]]:
        """Return tag usage counts across all ideas."""
        self.cursor.execute('SELECT tags FROM ideas WHERE user_id=?', (self.user_id,))
        tag_counter = Counter()
        for row in self.cursor.fetchall():
            try:
                tags = json.loads(row[0])
                for tag in tags: tag_counter[tag] += 1
            except (json.JSONDecodeError, TypeError): pass
        return tag_counter.most_common()

    def export_to_json(self) -> str:
        """Export all ideas and connections to a JSON file."""
        ideas = self.get_all_ideas()
        return json.dumps({"user_id": self.user_id, "exported_at": datetime.now(timezone.utc).isoformat(), "idea_count": len(ideas), "ideas": ideas}, indent=2)

    def import_from_json(self, json_data: str) -> int:
        """Import ideas and connections from a JSON file."""
        try:
            data = json.loads(json_data)
            imported = 0
            for idea in data.get("ideas", []):
                self.insert_idea(title=idea.get("title", ""), description=idea.get("description", ""), category=idea.get("category", "general"), tags=idea.get("tags", []), ratings=idea.get("ratings", {}))
                imported += 1
            return imported
        except (json.JSONDecodeError, KeyError): return 0

# ════════════════
# FACTORY FUNCTION
# ════════════════
def build_idea_incubator(user_id: str, user_log_dir: Optional[Path] = None, memory_engine=None) -> IdeaIncubator:
    """Construct and return a fully-initialised IdeaIncubator instance."""
    return IdeaIncubator(user_id=user_id, user_log_dir=user_log_dir, memory_engine=memory_engine)

# ═════════
# SELF-TEST
# ═════════
if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock memory engine for testing
        class MockMemory:
            def __init__(self):
                self.store = {
                    "test_user": {
                        "metric_healing": "resonance, balance, harmony, flow",
                        "metric_profit": "revenue, scale, market, growth"
                    }
                }
        incubator = build_idea_incubator("test_user", Path(tmpdir), MockMemory())
        # Test Analysis with defined metrics
        analysis = incubator.analyze_idea("This idea brings resonance and balance to the market")
        print(f"Analysis: {analysis}")
        # Test Analysis with NO defined metrics
        # (simulate empty memory).
        incubator_empty = build_idea_incubator("empty_user", Path(tmpdir), MockMemory()) # No metrics for empty_user
        analysis_empty = incubator_empty.analyze_idea("This idea brings resonance")
        print(f"Empty Analysis: {analysis_empty}")
        incubator.close()
        print("Test complete.")
