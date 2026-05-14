from __future__ import annotations

from skillmash.core.decomposer import AtomicDecomposer
from skillmash.core.graph import CapabilityGraph
from skillmash.core.matcher import CompositionMatcher
from skillmash.core.models import (
    ArtifactSpec,
    Composition,
    CompositionOperator,
    Condition,
    ParameterSpec,
    SkillDefinition,
    SkillKind,
)
from skillmash.core.planner import SkillPlanner
from skillmash.core.registry import SkillRegistry
from skillmash.core.scoring import PlanScorer


def build_sample_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_many(sample_skills())
    return registry


def build_sample_planner() -> SkillPlanner:
    registry = build_sample_registry()
    graph = CapabilityGraph(registry)
    decomposer = AtomicDecomposer(registry, graph)
    matcher = CompositionMatcher(registry)
    scorer = PlanScorer(registry)
    return SkillPlanner(registry, graph, matcher, decomposer, scorer)


def sample_skills() -> list[SkillDefinition]:
    return [
        SkillDefinition(
            id="web_search",
            name="Web Search",
            kind=SkillKind.ATOMIC,
            description="Search the web for fresh sources about a topic.",
            inputs=[ParameterSpec("query", "topic", True, "Search topic")],
            outputs=[ArtifactSpec("results", "search_results", "Search result list")],
            preconditions=[
                Condition("environment", "network_available", "Network is available")
            ],
            postconditions=[
                Condition("data", "search_results_non_empty", "At least one result")
            ],
            capability_tags={"web_search", "information_retrieval"},
            data_tags={"topic", "search_results", "text", "url"},
            cost={"latency": 3, "money": 1, "complexity": 1},
            quality={"reliability": 0.82, "freshness": 0.95},
        ),
        SkillDefinition(
            id="academic_search",
            name="Academic Search",
            kind=SkillKind.ATOMIC,
            description="Search academic sources for a topic.",
            inputs=[ParameterSpec("query", "topic", True, "Research topic")],
            outputs=[ArtifactSpec("papers", "search_results", "Academic result list")],
            capability_tags={"web_search", "academic_research"},
            data_tags={"topic", "search_results", "paper", "text"},
            cost={"latency": 5, "money": 2, "complexity": 2},
            quality={"reliability": 0.88, "freshness": 0.8},
        ),
        SkillDefinition(
            id="read_webpage",
            name="Read Webpage",
            kind=SkillKind.ATOMIC,
            description="Fetch and read webpage content from search results.",
            inputs=[ParameterSpec("results", "search_results", True, "Search results")],
            outputs=[ArtifactSpec("documents", "webpage_content", "Readable documents")],
            capability_tags={"content_fetching", "web_reading"},
            data_tags={"search_results", "webpage_content", "text"},
            cost={"latency": 4, "money": 1, "complexity": 2},
            quality={"reliability": 0.78},
        ),
        SkillDefinition(
            id="summarize_text",
            name="Summarize Text",
            kind=SkillKind.ATOMIC,
            description="Summarize a collection of documents into a concise brief.",
            inputs=[
                ParameterSpec("documents", "webpage_content", True, "Documents to summarize")
            ],
            outputs=[ArtifactSpec("summary", "summary", "Research summary")],
            capability_tags={"summarization", "text_processing"},
            data_tags={"webpage_content", "summary", "text"},
            cost={"latency": 2, "money": 1, "complexity": 1},
            quality={"reliability": 0.86},
        ),
        SkillDefinition(
            id="generate_outline",
            name="Generate Outline",
            kind=SkillKind.ATOMIC,
            description="Create a structured outline from a summary.",
            inputs=[ParameterSpec("content", "summary", True, "Source summary")],
            outputs=[ArtifactSpec("outline", "outline", "Structured outline")],
            capability_tags={"outline_generation", "planning"},
            data_tags={"summary", "outline", "text"},
            cost={"latency": 2, "money": 1, "complexity": 1},
            quality={"reliability": 0.84},
        ),
        SkillDefinition(
            id="create_ppt",
            name="Create PPT",
            kind=SkillKind.ATOMIC,
            description="Generate a PPTX deck from a slide outline.",
            inputs=[ParameterSpec("outline", "outline", True, "Slide outline")],
            outputs=[ArtifactSpec("deck", "pptx", "Generated presentation deck")],
            capability_tags={"slide_generation"},
            data_tags={"outline", "pptx"},
            cost={"latency": 5, "money": 2, "complexity": 2},
            quality={"reliability": 0.8},
        ),
        SkillDefinition(
            id="write_report",
            name="Write Report",
            kind=SkillKind.ATOMIC,
            description="Turn a summary into a report.",
            inputs=[ParameterSpec("content", "summary", True, "Summary content")],
            outputs=[ArtifactSpec("report", "report", "Written report")],
            capability_tags={"report_generation", "writing"},
            data_tags={"summary", "report", "text"},
            cost={"latency": 4, "money": 1, "complexity": 2},
            quality={"reliability": 0.83},
        ),
        SkillDefinition(
            id="answer_from_summary",
            name="Answer From Summary",
            kind=SkillKind.ATOMIC,
            description="Answer a user question based on a summary.",
            inputs=[ParameterSpec("content", "summary", True, "Summary content")],
            outputs=[ArtifactSpec("answer", "answer", "Final answer")],
            capability_tags={"qa", "summarization"},
            data_tags={"summary", "answer", "text"},
            cost={"latency": 2, "money": 1, "complexity": 1},
            quality={"reliability": 0.82},
        ),
        SkillDefinition(
            id="research_topic",
            name="Research Topic",
            kind=SkillKind.COMPOSITE,
            description="Search, read and summarize a topic.",
            inputs=[ParameterSpec("topic", "topic", True, "Research topic")],
            outputs=[ArtifactSpec("summary", "summary", "Research summary")],
            capability_tags={"web_search", "summarization", "research"},
            data_tags={"topic", "summary", "text"},
            contains=["web_search", "read_webpage", "summarize_text"],
            composition=Composition(
                CompositionOperator.SEQUENTIAL,
                ("web_search", "read_webpage", "summarize_text"),
            ),
            cost={"latency": 8, "money": 3, "complexity": 3},
            quality={"reliability": 0.8, "freshness": 0.9},
        ),
        SkillDefinition(
            id="search_and_make_ppt",
            name="Search And Make PPT",
            kind=SkillKind.WRAPPED,
            description="A wrapped external agent capability for searching and creating PPT.",
            inputs=[ParameterSpec("topic", "topic", True, "Presentation topic")],
            outputs=[ArtifactSpec("deck", "pptx", "Generated presentation deck")],
            capability_tags={"web_search", "summarization", "slide_generation"},
            data_tags={"topic", "pptx", "text"},
            contains=[
                "web_search",
                "read_webpage",
                "summarize_text",
                "generate_outline",
                "create_ppt",
            ],
            composition=Composition(
                CompositionOperator.SEQUENTIAL,
                (
                    "web_search",
                    "read_webpage",
                    "summarize_text",
                    "generate_outline",
                    "create_ppt",
                ),
            ),
            cost={"latency": 12, "money": 5, "complexity": 5},
            quality={"reliability": 0.78, "freshness": 0.92},
        ),
    ]
