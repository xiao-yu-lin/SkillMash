"""Representation extraction orchestration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from skillmash.representation.manifest import SkillManifestParser
from skillmash.representation.models import (
    ExtractedSkillSchema,
    ExtractionDiagnostic,
    NormalizationDecision,
    RawSkillManifest,
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
        scanner: Optional[SkillFolderScanner] = None,
        parser: Optional[SkillManifestParser] = None,
        normalizer: Optional[SkillRepresentationNormalizer] = None,
        progress: Optional[ProgressCallback] = None,
        max_workers: int = 1,
    ) -> None:
        self.schema_extractor = schema_extractor
        self.scanner = scanner or SkillFolderScanner()
        self.parser = parser or SkillManifestParser()
        self.normalizer = normalizer or SkillRepresentationNormalizer()
        self.progress = progress
        self.max_workers = max(1, max_workers)

    def extract_all(self, skills_root: Union[Path, str]) -> RepresentationExtractionResult:
        representations_by_index: Dict[int, object] = {}
        diagnostics: List[ExtractionDiagnostic] = []
        normalization_decisions: List[NormalizationDecision] = []

        folders = self.scanner.scan(skills_root)
        total = len(folders)
        self._emit_progress("scan", 0, total, str(skills_root))

        if self._should_extract_in_batches():
            return self._extract_all_batched(folders, total)

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

    def _should_extract_in_batches(self) -> bool:
        return bool(
            getattr(self.schema_extractor, "use_batch", False)
            and hasattr(self.schema_extractor, "extract_many")
        )

    def _extract_all_batched(
        self,
        folders,
        total: int,
    ) -> RepresentationExtractionResult:
        manifests_by_index: Dict[int, RawSkillManifest] = {}
        extracted_by_index: Dict[int, ExtractedSkillSchema] = {}
        representations_by_index: Dict[int, object] = {}
        diagnostics: List[ExtractionDiagnostic] = []
        normalization_decisions: List[NormalizationDecision] = []

        for index, folder in enumerate(folders):
            current = index + 1
            self._emit_progress("parse", current, total, folder.relative_path)
            manifests_by_index[index] = self.parser.parse(folder)

        batch_size = int(getattr(getattr(self.schema_extractor, "config", None), "batch_size", 32))
        for start in range(0, total, max(1, batch_size)):
            end = min(total, start + max(1, batch_size))
            batch_indexes = list(range(start, end))
            item = f"{start + 1}-{end}/{total}"
            self._emit_progress("extract_batch", end, total, item)
            batch_manifests = [manifests_by_index[index] for index in batch_indexes]
            batch_extracted = self.schema_extractor.extract_many(batch_manifests)
            if len(batch_extracted) != len(batch_manifests):
                raise RuntimeError(
                    "Batch schema extractor returned "
                    f"{len(batch_extracted)} schemas for {len(batch_manifests)} manifests."
                )
            for index, extracted in zip(batch_indexes, batch_extracted):
                extracted_by_index[index] = extracted

        for index, folder in enumerate(folders):
            current = index + 1
            self._emit_progress("normalize", current, total, folder.relative_path)
            result = self.normalizer.normalize(
                manifests_by_index[index],
                extracted_by_index[index],
            )
            representations_by_index[index] = result.representation
            diagnostics.extend(result.diagnostics)
            normalization_decisions.extend(result.decisions)
            self._emit_progress("done", current, total, folder.relative_path)

        return RepresentationExtractionResult(
            representations=[
                representations_by_index[index] for index in sorted(representations_by_index)
            ],
            diagnostics=diagnostics,
            normalization_decisions=normalization_decisions,
            io_name_vocab=self.normalizer.io_name_vocabulary.to_dict(),
        )
