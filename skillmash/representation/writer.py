"""Write representation extraction artifacts to disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from skillmash.representation.models import RepresentationExtractionResult


def write_extraction_result(
    result: RepresentationExtractionResult,
    out_dir: Union[Path, str],
) -> None:
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "representations.json").write_text(
        json.dumps(
            {
                "representations": [
                    representation.to_dict()
                    for representation in result.representations
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_path / "diagnostics.json").write_text(
        json.dumps(
            {
                "diagnostics": [
                    diagnostic.to_dict()
                    for diagnostic in result.diagnostics
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_path / "normalization_decisions.json").write_text(
        json.dumps(
            {
                "normalization_decisions": [
                    decision.to_dict()
                    for decision in result.normalization_decisions
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_path / "io_name_vocab.json").write_text(
        json.dumps(
            result.io_name_vocab,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_path / "task_vocab.json").write_text(
        json.dumps(
            result.task_vocab,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
