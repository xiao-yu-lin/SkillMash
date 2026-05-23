from __future__ import annotations

from skillmash.lexicon import ArtifactLexicon


def test_artifact_lexicon_tokenize_respects_stop_terms_and_length() -> None:
    lexicon = ArtifactLexicon.create(
        stop_terms={"and", "the"},
        min_token_length=3,
    )

    tokens = lexicon.tokenize("The API and spec review")

    assert tokens == {"api", "spec", "review"}


def test_artifact_lexicon_generic_names_are_case_insensitive() -> None:
    lexicon = ArtifactLexicon.create(
        stop_terms=set(),
        min_token_length=2,
        generic_io_names={"review_report"},
    )

    assert lexicon.is_generic_io_name("review_report") is True
    assert lexicon.is_generic_io_name("REVIEW_REPORT") is True
    assert lexicon.is_generic_io_name("summary") is False
