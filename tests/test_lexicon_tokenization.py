from __future__ import annotations

from skillmash.lexicon import ArtifactLexicon, DEFAULT_PLANNING_STOP_TERMS


def test_tokenize_normalizes_fullwidth_ascii() -> None:
    lexicon = ArtifactLexicon.create(
        stop_terms=DEFAULT_PLANNING_STOP_TERMS,
        min_token_length=2,
    )

    tokens = lexicon.tokenize("ＡＰＩ　ＳＰＥＣ")

    assert "api" in tokens
    assert "spec" in tokens


def test_tokenize_handles_mixed_chinese_and_english_terms() -> None:
    lexicon = ArtifactLexicon.create(
        stop_terms=DEFAULT_PLANNING_STOP_TERMS,
        min_token_length=2,
    )

    tokens = lexicon.tokenize("API安全审计")

    assert "api" in tokens
    assert "安全" in tokens
    assert "审计" in tokens
