"""Strategy interfaces for orchestration pruning and ranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class PruneContext:
    query: str
    grounded_query: dict[str, Any]
    policy: dict[str, Any]
    runtime_constraints: dict[str, Any]


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    hard_fail_codes: list[str]


class PlanStrategy(Protocol):
    name: str

    def hard_filter(self, plan: dict[str, Any], ctx: PruneContext) -> FilterResult:
        ...

    def rank_score(self, plan: dict[str, Any], ctx: PruneContext) -> float:
        ...
