from pathlib import Path


def test_graph_ui_only_exposes_supported_relation_types() -> None:
    html = Path("ui/index.html").read_text(encoding="utf-8")

    assert "can_feed" in html
    for retired_relation in [
        "has_output",
        "requires_input",
        "produces",
        "consumes",
        "aggregates",
        "depends_on",
        "similar_to",
        "substitute_for",
    ]:
        assert retired_relation not in html
