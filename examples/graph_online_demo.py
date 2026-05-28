"""Run Skill orchestration from offline graph build artifacts.

Usage:
    python examples/graph_online_demo.py --build_dir OUTPUT/v4/graph
    python examples/graph_online_demo.py --build_dir OUTPUT/v4/graph --query "I have api_spec and want a security review"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skillmash.orchestration import SkillOrchestrator, load_build_artifacts  # noqa: E402
from skillmash.representation import LLMConfig  # noqa: E402


DEFAULT_BUILD_DIR = REPO_ROOT / "OUTPUT" / "v4" / "graph"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan Skill orchestration from graph build artifacts."
    )
    parser.add_argument(
        "--build_dir",
        default=str(DEFAULT_BUILD_DIR),
        help="Directory containing graph build artifacts. Defaults to OUTPUT/v4/graph.",
    )
    parser.add_argument(
        "--query",
        help="User query. If omitted, the script reads one line from input().",
    )
    parser.add_argument(
        "--min_edge_confidence",
        type=float,
        default=0.7,
        help="Minimum can_feed edge confidence used for traversal.",
    )
    parser.add_argument(
        "--max_depth",
        type=int,
        default=10,
        help="Maximum Skill steps in each candidate plan.",
    )
    parser.add_argument(
        "--max_plans",
        type=int,
        default=60,
        help="Maximum number of candidate plans to generate before ranking.",
    )
    parser.add_argument(
        "--top_m",
        type=int,
        default=40,
        help="Maximum candidate plans sent to the LLM ranker.",
    )
    parser.add_argument(
        "--top_k",
        type=int,
        default=5,
        help="Number of recommended candidate plans to return.",
    )
    parser.add_argument(
        "--max_branch",
        type=int,
        default=20,
        help="Maximum expansion branch count per search state.",
    )
    parser.add_argument(
        "--beam_width",
        type=int,
        default=40,
        help="Maximum partial plans retained at each search depth.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full JSON planning result instead of a concise summary.",
    )
    parser.add_argument(
        "--show_candidates",
        action="store_true",
        help="Also print raw candidate plans after recommended plans.",
    )
    parser.add_argument(
        "--log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Runtime log level. Use DEBUG for planner/reranker diagnostics.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    query = args.query if args.query is not None else input("query> ")
    artifacts = load_build_artifacts(args.build_dir)
    llm_config = LLMConfig.from_env(REPO_ROOT / ".env")
    planner = SkillOrchestrator(
        artifacts,
        llm_config=llm_config,
        min_edge_confidence=args.min_edge_confidence,
        max_depth=args.max_depth,
        max_plans=args.max_plans,
        max_branch=args.max_branch,
        beam_width=args.beam_width,
        top_m=args.top_m,
        top_k=args.top_k,
        include_candidates=args.show_candidates,
    )
    result = planner.plan(query)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_plan_summary(result, show_candidates=args.show_candidates))


def _format_plan_summary(result: dict, *, show_candidates: bool = False) -> str:
    lines = [f"query: {result.get('query', '')}", ""]
    recommended = result.get("recommended_plans", [])
    if recommended:
        lines.append("Recommended plans:")
        lines.append("")
        for index, plan in enumerate(recommended, start=1):
            status = plan.get("status", "unknown")
            title = plan.get("title") or f"Plan {index}"
            lines.append(f"{index}. {title} [{status}]")
            lines.extend(_format_plan_graph(plan, indent="   "))
            missing = plan.get("missing_inputs", [])
            if missing:
                lines.append(f"   missing: {_format_missing_inputs(missing)}")
            reason = plan.get("reason")
            if reason:
                lines.append(f"   reason: {reason}")
            lines.append("")
        if not show_candidates:
            return "\n".join(lines).rstrip()

    plans = result.get("plans", [])
    if not plans:
        return "\n".join([*lines, "No candidate plans found."])

    if recommended:
        lines.append("Candidate plans:")
        lines.append("")
    for index, plan in enumerate(plans, start=1):
        status = plan.get("status", "unknown")
        lines.append(f"Plan {index} [{status}]")
        lines.extend(_format_plan_graph(plan, indent="  "))
        missing = plan.get("missing_inputs", [])
        if missing:
            lines.append(f"  missing: {_format_missing_inputs(missing)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_missing_inputs(missing: list[dict]) -> str:
    return ", ".join(
        f"{item.get('skill_id')}:{item.get('name')}({item.get('type')})"
        for item in missing
    )


def _format_plan_graph(plan: dict, *, indent: str) -> list[str]:
    nodes = _plan_node_ids(plan)
    edges = [
        (edge.get("source_id"), edge.get("target_id"))
        for edge in plan.get("can_feed_edges", [])
        if edge.get("source_id") and edge.get("target_id")
    ]
    lines = [f"{indent}graph:", f"{indent}  ```mermaid", f"{indent}  flowchart TD"]
    if edges:
        for source, target in edges:
            lines.append(f"{indent}    {_node_key(source)}[\"{source}\"] --> {_node_key(target)}[\"{target}\"]")
    else:
        for node in nodes:
            lines.append(f"{indent}    {_node_key(node)}[\"{node}\"]")
    lines.append(f"{indent}  ```")
    return lines


def _plan_node_ids(plan: dict) -> list[str]:
    stages = plan.get("stages", [])
    nodes = []
    for stage in stages:
        skill_ids = stage.get("skill_order")
        if skill_ids is None:
            skill_ids = [
                skill.get("skill_id")
                for skill in stage.get("skills", [])
                if skill.get("skill_id")
            ]
        for skill_id in skill_ids:
            if skill_id and skill_id not in nodes:
                nodes.append(str(skill_id))
    if nodes:
        return nodes
    return [
        str(skill_id)
        for skill_id in plan.get("skill_order", [])
        if skill_id
    ]


def _node_key(value: object) -> str:
    return "n_" + "".join(
        char if char.isalnum() else "_"
        for char in str(value)
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
