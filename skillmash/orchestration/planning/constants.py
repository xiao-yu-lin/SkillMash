"""Shared constants for orchestration planning."""

from __future__ import annotations

from skillmash.lexicon import DEFAULT_PLANNING_STOP_TERMS

DEFAULT_STOP_TERMS = set(DEFAULT_PLANNING_STOP_TERMS)

DEFAULT_USER_ARTIFACTS = {
    ("goal", "text"),
    ("query", "text"),
}

LLM_GROUNDING_SYSTEM_PROMPT = """You map a user request to an existing Skill vocabulary.
Return strict JSON only.

Rules:
- Select available_artifacts only when the user explicitly says they have, provide,
  attach, uploaded, or want to use that artifact. Do not invent artifacts.
- Select goal_terms from the provided task/output/vocabulary terms only.
- Preserve canonical artifact names and types exactly as provided.
- Select inferred_inputs only for control/configuration inputs whose value is
  directly implied by the request, such as command=send or target_language=zh-CHS.
- Do not infer private/user data or content payloads such as recipients,
  attachments, files, email body, or document text.
- If uncertain, omit the item.

Schema:
{
  "available_artifacts": [{"name": "api_spec", "type": "yaml"}],
  "inferred_inputs": [
    {"skill_id": "email", "name": "command", "type": "text", "value": "send"}
  ],
  "goal_terms": ["review", "audit"]
}
"""
