"""Representation extraction orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from skillmash.representation.manifest import SkillManifestParser
from skillmash.representation.models import (
    ExtractionDiagnostic,
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
    ) -> None:
        self.schema_extractor = schema_extractor
        self.scanner = scanner or SkillFolderScanner()
        self.parser = parser or SkillManifestParser()
        self.normalizer = normalizer or SkillRepresentationNormalizer()
        self.progress = progress

    def extract_all(self, skills_root: Path | str) -> RepresentationExtractionResult:
        representations = []
        diagnostics: list[ExtractionDiagnostic] = []

        folders = self.scanner.scan(skills_root)
        total = len(folders)
        self._emit_progress("scan", 0, total, str(skills_root))
        for index, folder in enumerate(folders, start=1):
            self._emit_progress("parse", index, total, folder.relative_path)
            manifest = self.parser.parse(folder)
            self._emit_progress("extract", index, total, folder.relative_path)
            extracted = self.schema_extractor.extract(manifest)
            self._emit_progress("normalize", index, total, folder.relative_path)
            result = self.normalizer.normalize(manifest, extracted)
            representations.append(result.representation)
            diagnostics.extend(result.diagnostics)
            self._emit_progress("done", index, total, folder.relative_path)

        return RepresentationExtractionResult(
            representations=representations,
            diagnostics=diagnostics,
        )

    def _emit_progress(self, stage: str, current: int, total: int, item: str) -> None:
        if self.progress is not None:
            self.progress(stage, current, total, item)
