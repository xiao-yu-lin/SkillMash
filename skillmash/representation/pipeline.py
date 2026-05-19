"""Representation extraction orchestration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from skillmash.representation.manifest import SkillManifestParser
from skillmash.representation.models import (
    ExtractionDiagnostic,
    NormalizationDecision,
    RepresentationExtractionResult,
    SkillSchemaExtractor,
)
from skillmash.representation.normalizer import SkillRepresentationNormalizer
from skillmash.representation.scanner import SkillFolderScanner

ProgressCallback = Callable[[str, int, int, str], None]


class RepresentationExtractor:
    """Scan, parse, extract, and normalize Skill representations."""

    def __init__(
        self,
        schema_extractor: SkillSchemaExtractor,
        scanner: SkillFolderScanner | None = None,
        parser: SkillManifestParser | None = None,
        normalizer: SkillRepresentationNormalizer | None = None,
        progress: ProgressCallback | None = None,
        max_workers: int = 1,
    ) -> None:
        self.schema_extractor = schema_extractor
        self.scanner = scanner or SkillFolderScanner()
        self.parser = parser or SkillManifestParser()
        self.normalizer = normalizer or SkillRepresentationNormalizer()
        self.progress = progress
        self.max_workers = max(1, max_workers)

    def extract_all(self, skills_root: Path | str) -> RepresentationExtractionResult:
        representations_by_index = {}
        diagnostics: list[ExtractionDiagnostic] = []
        normalization_decisions: list[NormalizationDecision] = []

        folders = self.scanner.scan(skills_root)
        total = len(folders)
        self._emit_progress("scan", 0, total, str(skills_root))

        if self.max_workers == 1:
            for index, folder in enumerate(folders):
                result = self._process_folder(index, folder, total)
                representations_by_index[index] = result.representation
                diagnostics.extend(result.diagnostics)
                normalization_decisions.extend(result.decisions)
            return RepresentationExtractionResult(
                representations=[
                    representations_by_index[index] for index in sorted(representations_by_index)
                ],
                diagnostics=diagnostics,
                normalization_decisions=normalization_decisions,
                io_name_vocab=self.normalizer.io_name_vocabulary.to_dict(),
            )

        completed = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._process_folder, index, folder, total): (index, folder)
                for index, folder in enumerate(folders)
            }
            for future in as_completed(futures):
                index, folder = futures[future]
                result = future.result()
                representations_by_index[index] = result.representation
                diagnostics.extend(result.diagnostics)
                normalization_decisions.extend(result.decisions)
                completed += 1
                self._emit_progress("done", completed, total, folder.relative_path)

        return RepresentationExtractionResult(
            representations=[
                representations_by_index[index] for index in sorted(representations_by_index)
            ],
            diagnostics=diagnostics,
            normalization_decisions=normalization_decisions,
            io_name_vocab=self.normalizer.io_name_vocabulary.to_dict(),
        )

    def _process_folder(self, index: int, folder, total: int):
        current = index + 1
        self._emit_progress("parse", current, total, folder.relative_path)
        manifest = self.parser.parse(folder)
        self._emit_progress("extract", current, total, folder.relative_path)
        extracted = self.schema_extractor.extract(manifest)
        self._emit_progress("normalize", current, total, folder.relative_path)
        result = self.normalizer.normalize(manifest, extracted)
        if self.max_workers == 1:
            self._emit_progress("done", current, total, folder.relative_path)
        return result

    def _emit_progress(self, stage: str, current: int, total: int, item: str) -> None:
        if self.progress is not None:
            self.progress(stage, current, total, item)
