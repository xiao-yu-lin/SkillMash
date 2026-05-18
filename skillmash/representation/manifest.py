"""SKILL.md parsing."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from skillmash.representation.models import (
    ExtractionDiagnostic,
    RawSkillManifest,
    SkillFolder,
)


class SkillManifestParser:
    """Parse a SkillFolder's SKILL.md into frontmatter and body."""

    def parse(self, folder: SkillFolder) -> RawSkillManifest:
        text = folder.entry.read_text(encoding="utf-8-sig")
        frontmatter, body, diagnostics = self._split_frontmatter(text, folder)
        body_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return RawSkillManifest(
            folder=folder,
            frontmatter=frontmatter,
            body=body,
            body_sha256=body_sha256,
            diagnostics=diagnostics,
        )

    def _split_frontmatter(
        self,
        text: str,
        folder: SkillFolder,
    ) -> tuple[dict[str, Any], str, list[ExtractionDiagnostic]]:
        diagnostics: list[ExtractionDiagnostic] = []
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.startswith("---\n"):
            return {}, normalized, diagnostics

        end = normalized.find("\n---\n", 4)
        if end == -1:
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="manifest",
                    severity="warning",
                    code="invalid_frontmatter",
                    message="frontmatter start marker found without closing marker",
                    path=str(folder.path),
                )
            )
            return {}, normalized, diagnostics

        raw_frontmatter = normalized[4:end]
        body = normalized[end + len("\n---\n") :]
        try:
            parsed = yaml.safe_load(raw_frontmatter) or {}
        except yaml.YAMLError as exc:
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="manifest",
                    severity="warning",
                    code="invalid_frontmatter",
                    message="frontmatter could not be parsed as YAML",
                    path=str(folder.path),
                    details={"error": str(exc)},
                )
            )
            return {}, body, diagnostics

        if not isinstance(parsed, dict):
            diagnostics.append(
                ExtractionDiagnostic(
                    stage="manifest",
                    severity="warning",
                    code="invalid_frontmatter",
                    message="frontmatter must be a YAML mapping",
                    path=str(folder.path),
                )
            )
            return {}, body, diagnostics

        return parsed, body, diagnostics
