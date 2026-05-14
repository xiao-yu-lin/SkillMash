import unittest

from skillmash.build.extraction import ExtractedSkillSchema, schema_from_llm_payload
from skillmash.build.offline import RawSkillManifest, SkillFolder, SkillNormalizer
from skillmash.core.models import ArtifactSpec, ParameterSpec
from skillmash.runtime.app_service import SkillMashService


class SkillMashCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SkillMashService()

    def test_decompose_wrapped_skill_to_atomic_skills(self) -> None:
        result = self.service.decompose("search_and_make_ppt")

        self.assertEqual(
            result["atomic_skills"],
            [
                "web_search",
                "read_webpage",
                "summarize_text",
                "generate_outline",
                "create_ppt",
            ],
        )

    def test_match_sequential_skills(self) -> None:
        result = self.service.match("web_search", "read_webpage")

        self.assertTrue(result["composable"])
        self.assertEqual(result["operator"], "sequential")
        self.assertEqual(result["compatibility"], "exact_match")
        self.assertEqual(result["input_mapping"]["results"], "web_search.results")

    def test_plan_research_ppt_task(self) -> None:
        result = self.service.plan("帮我搜索 AI Agent 最新趋势，并生成 PPT")

        self.assertEqual(result["goal"]["required_outputs"], ["pptx"])
        self.assertGreaterEqual(len(result["plans"]), 1)
        best = result["plans"][0]
        self.assertEqual(best["status"], "ready")
        self.assertIn("pptx", best["produced_artifacts"])
        self.assertIn("web_search", best["atomic_skills"])
        self.assertIn("create_ppt", best["atomic_skills"])

    def test_graph_summary_contains_typed_edges(self) -> None:
        summary = self.service.graph_summary()
        edge_types = {edge["type"] for edge in summary["edges"]}

        self.assertIn("contains", edge_types)
        self.assertIn("consumes", edge_types)
        self.assertIn("produces", edge_types)

    def test_llm_payload_schema_is_normalized(self) -> None:
        schema = schema_from_llm_payload(
            {
                "inputs": [{"name": "Research Topic", "type": "Topic"}],
                "outputs": [{"name": "Slide Deck", "type": "PPTX"}],
                "skill_tags": ["Slide Generation"],
                "data_tags": ["PPTX"],
            }
        )

        self.assertEqual(schema.inputs[0].name, "research_topic")
        self.assertEqual(schema.outputs[0].type, "pptx")
        self.assertIn("slide_generation", schema.skill_tags)

    def test_normalizer_accepts_pluggable_schema_extractor(self) -> None:
        class FakeExtractor:
            def extract(self, **kwargs):
                return ExtractedSkillSchema(
                    inputs=[ParameterSpec("topic", "topic")],
                    outputs=[ArtifactSpec("brief", "summary")],
                    skill_tags={"summarization"},
                    data_tags={"text"},
                    source="test",
                )

        normalizer = SkillNormalizer(FakeExtractor())
        skill, diagnostics = normalizer.normalize(
            RawSkillManifest(
                folder=SkillFolder(
                    id_hint="demo",
                    path="demo",
                    entry="demo/SKILL.md",
                    relative_path="demo",
                ),
                frontmatter={"name": "Demo", "description": "Summarize a topic"},
                body="# Demo",
            )
        )

        self.assertEqual(diagnostics, [])
        self.assertEqual(skill.inputs[0].type, "topic")
        self.assertEqual(skill.outputs[0].type, "summary")
        self.assertEqual(skill.metadata["schema_extractor"], "test")


if __name__ == "__main__":
    unittest.main()
