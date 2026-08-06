#!/usr/bin/env python3
# conversation_engine/identity_utils.py
"""
Identity Utilities for Partnership_AI
─────────────────────────────────────
Handles system identity, founding pacts, and self-model construction.
"""
from __future__ import annotations
import json
from typing import Dict, Any, Optional
from pathlib import Path

# Import encryption utilities (assuming they're in
# conversation_engine or root).
try:
    from conversation_engine.logging_utils import log_plan_update
except ImportError:
    # Fallback if logging_utils is at root level
    from logging_utils import log_plan_update

# ══════════════════════════
# DEFAULT IDENTITY STRUCTURE
# ══════════════════════════
def default_identity() -> Dict[str, Any]:
    """
    Returns the default identity structure for a new Partnership_AI instance.
    This establishes the foundational pact between user and system.
    """
    return {
        "user_profile": {
            "name": None,
            "roles": [],
            "preferences": {},
            "communication_style": {}
        },
        "long_term_goals": [],
        "persistent_facts": [],
        "active_threads": [],
        "partnership_metadata": {
            "established": None,
            "version": "1.0",
            "encryption_level": "end-to-end",
            "adaptive_mode": True
        },
        "system_identity": {
            "name": "Partnership_AI",
            "type": "Adaptive Collaborative Agent",
            "capabilities": [
                "secure_memory",
                "adaptive_reasoning",
                "code_generation",
                "creative_writing",
                "problem_solving"
            ],
            "constraints": [
                "zero_access_encryption",
                "user_data_sovereignty",
                "transparent_decision_making"
            ]
        }
    }

# ════════════════════════
# FOUNDING PACT MANAGEMENT
# ════════════════════════
def ensure_founding_pact_in_memory(
    learned_data: str,
    key: bytes,
    learned_file: Path,
    user_id: Optional[str] = None
) -> str:
    """
    Ensures the founding pact is properly established in the learned data.
    If the data is empty, malformed, or missing required keys, initializes with default identity.

    Args:
        learned_data: Current encrypted/decrypted learned data string
        key: Encryption key for saving updates
        learned_file: Path to the learned data file
        user_id: Optional user identifier for logging

    Returns:
        Updated learned_data string (JSON)
    """
    try:
        # Try to parse existing data
        data = json.loads(learned_data)
        # Ensure the data is a dictionary
        if not isinstance(data, dict):
            data = default_identity()
        # Check if partnership_metadata exists, if
        # not, initialize it.
        if "partnership_metadata" not in data or not isinstance(data["partnership_metadata"], dict):
            data["partnership_metadata"] = {
                "established": None,
                "version": "1.0",
                "encryption_level": "end-to-end",
                "adaptive_mode": True
            }
        # Check if system_identity exists, if not,
        # initialize it.
        if "system_identity" not in data or not isinstance(data["system_identity"], dict):
             data["system_identity"] = {
                "name": "Partnership_AI",
                "type": "Adaptive Collaborative Agent",
                "capabilities": [],
                "constraints": []
            }
        # Check if founding pact metadata exists
        if data["partnership_metadata"].get("established") is None:
            # Initialize founding pact
            from datetime import datetime, timezone
            data["partnership_metadata"]["established"] = datetime.now(timezone.utc).isoformat()
            # Log the founding pact establishment
            if user_id:
                try:
                    initial_plan = None  # Would come from memory_engine in main context.
                    if initial_plan:
                        log_plan_update(
                            user_id, 
                            initial_plan, 
                            "[SYSTEM]", 
                            "Founding pact established."
                        )
                except Exception:
                    pass  # Silently fail if logging not available yet.
        return json.dumps(data, indent=2)
    except json.JSONDecodeError:
        # Corrupted data - return fresh default
        # identity.
        return json.dumps(default_identity(), indent=2)
    except Exception as e:
        # Any other error (e.g., missing keys) -
        # return fresh default identity.
        print(f"[IDENTITY] Error processing learned data: {e}. Resetting to default.")
        return json.dumps(default_identity(), indent=2)

# ═══════════════════════
# SELF-MODEL CONSTRUCTION
# ═══════════════════════
def build_self_model_string() -> str:
    """
    Constructs a self-model string that represents the system's identity
    and operational constraints. This is used as a system override for
    dialogue generation to maintain consistent persona.

    Returns:
        A formatted string describing the system's self-model
    """
    self_model = """
PARTNERSHIP_AI SYSTEM IDENTITY MODEL
═══════════════════════════════════

ROLE: Adaptive Collaborative Partner
MODE: Secure, Encrypted, User-Sovereign

CORE PRINCIPLES:
1. Zero-Access Encryption - I cannot access your data without your key
2. Transparent Reasoning - I explain my thinking process
3. Adaptive Learning - I evolve based on our partnership needs
4. User Sovereignty - You control all data and decisions

CAPABILITIES:
- Secure conversation memory with encryption
- Adaptive agent reasoning and planning
- Code generation and review
- Creative writing and collaborative projects
- Problem-solving across domains

CONSTRAINTS:
- No external data access without explicit permission
- All learning stored locally and encrypted
- No persistent identity across sessions without user consent
- Safety and ethical guidelines always active

PARTNERSHIP MODE:
I am here to collaborate with you as an equal partner,
not to serve as a subordinate tool. We build together.

═══════════════════════════════════
END OF SYSTEM IDENTITY MODEL
"""
    return self_model.strip()

# ═════════════════════
# IDENTITY VERIFICATION
# ═════════════════════
def verify_identity_integrity(learned_data: str) -> bool:
    """
    Verifies that the learned data has the required identity structure.

    Args:
        learned_data: JSON string of learned data

    Returns:
        True if identity structure is valid, False otherwise
    """
    try:
        data = json.loads(learned_data)
        required_keys = ["user_profile", "partnership_metadata", "system_identity"]
        return all(key in data for key in required_keys)
    except (json.JSONDecodeError, TypeError):
        return False

# ═══════════════════════
# IDENTITY UPDATE HELPERS
# ═══════════════════════
def update_user_profile(learned_data: str, updates: Dict[str, Any]) -> str:
    """
    Updates the user profile section of learned data.

    Args:
        learned_data: Current learned data JSON string
        updates: Dictionary of fields to update

    Returns:
        Updated learned_data string
    """
    try:
        data = json.loads(learned_data)
        if "user_profile" not in data:
            data["user_profile"] = {}
        data["user_profile"].update(updates)
        return json.dumps(data, indent=2)
    except (json.JSONDecodeError, TypeError):
        return learned_data

def add_persistent_fact(learned_data: str, fact: str, category: str = "general") -> str:
    """
    Adds a persistent fact to the learned data.

    Args:
        learned_data: Current learned data JSON string
        fact: The fact to store
        category: Category for organization

    Returns:
        Updated learned_data string
    """
    try:
        data = json.loads(learned_data)
        if "persistent_facts" not in data:
            data["persistent_facts"] = []
        fact_entry = {
            "fact": fact,
            "category": category,
            "added": None  # Can be populated with timestamp if needed.
        }
        data["persistent_facts"].append(fact_entry)
        return json.dumps(data, indent=2)
    except (json.JSONDecodeError, TypeError):
        return learned_data

# ═══════════════════════════════════════
# PROFILE EXTRACTION HELPER
# ═══════════════════════════════════════

def update_profile_if_needed(learned_data: str, user_input: str) -> str:
    """Check if user input contains profile-worthy info and update the profile.

    Looks for patterns like "my name is X", "I work at Y", "I live in Z"
    and updates the user_profile section of learned_data accordingly.

    Args:
        learned_data: Current learned data JSON string
        user_input: The user's latest message

    Returns:
        Updated learned_data string (unchanged if no profile info found)
    """
    import re
    updates = {}

    # Name patterns
    name_match = re.search(r'(?:my name is|I am|I\'m|call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', user_input)
    if name_match:
        updates["name"] = name_match.group(1)

    # Location patterns
    loc_match = re.search(r'(?:I live in|I\'m in|I\'m from|based in)\s+([A-Z][a-zA-Z\s]+)', user_input)
    if loc_match:
        updates["location"] = loc_match.group(1).strip()

    # Occupation patterns
    occ_match = re.search(r'(?:I work at|I work for|I\'m a|I am a)\s+([a-zA-Z\s]+)', user_input)
    if occ_match:
        updates["occupation"] = occ_match.group(1).strip()

    if updates:
        return update_user_profile(learned_data, updates)
    return learned_data
