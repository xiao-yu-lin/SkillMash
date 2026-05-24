"""Hard validation policy and fail codes for orchestration plans."""

from __future__ import annotations

HARD_FAIL_MISSING_REQUIRED_INPUT = "missing_required_input"
HARD_FAIL_LOW_CONFIDENCE_EDGE = "low_confidence_edge"
HARD_FAIL_NO_EXPLICIT_CAN_FEED = "no_explicit_can_feed"


def default_policy() -> dict[str, object]:
    return {
        "allow_unknown_required_types": False,
        "min_edge_confidence": 0.7,
        "require_explicit_adjacency": True,
    }
