"""Static vocabulary for DataType (representation formats/carriers).

This module defines the controlled vocabulary for data types used in
Skill I/O specifications, such as text, markdown, json, pdf, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, FrozenSet, Optional

from skillmash.representation.normalize.base_vocab import StaticVocabulary


@dataclass(frozen=True)
class DataTypeResolution:
    """Resolution result for DataType vocabulary lookup."""

    normalized_value: Optional[str]
    method: str
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "normalized_value": self.normalized_value,
            "method": self.method,
            "confidence": self.confidence,
        }


class DataTypeVocabulary(StaticVocabulary):
    """Static vocabulary for DataType representation formats/carriers.

    This vocabulary is immutable and predefined, containing common
    data representation formats like text, markdown, json, pdf, etc.

    Example usage:
        vocab = DataTypeVocabulary()  # Uses defaults
        vocab.resolve("natural_language")  # Returns "text"
        vocab.resolve("pdf")  # Returns "pdf"
        vocab.resolve("unknown_format")  # Returns "unknown"
    """

    VERSION: ClassVar[str] = "data-type-v1"

    def __init__(
        self,
        version: Optional[str] = None,
        vocab: Optional[FrozenSet[str]] = None,
        aliases: Optional[Dict[str, str]] = None,
    ) -> None:
        """Initialize DataTypeVocabulary with optional custom parameters.

        Args:
            version: Vocabulary version string. Defaults to VERSION.
            vocab: Set of canonical vocabulary terms. Defaults to DEFAULT_VOCAB.
            aliases: Alias mappings. Defaults to DEFAULT_ALIASES.
        """
        super().__init__(
            version=version or self.VERSION,
            vocab=vocab or self.DEFAULT_VOCAB,
            aliases=aliases or self.DEFAULT_ALIASES,
        )

    DEFAULT_VOCAB: ClassVar[FrozenSet[str]] = frozenset(
        {
            "text",
            "markdown",
            "json",
            "csv",
            "yaml",
            "pdf",
            "html",
            "docx",
            "pptx",
            "xlsx",
            "png",
            "jpg",
            "svg",
            "url",
            "file",
            "path",
            "audio",
            "video",
            "code",
            "unknown",
        }
    )

    DEFAULT_ALIASES: ClassVar[Dict[str, str]] = {
        "natural_language": "text",
        "natural_language_query": "text",
        "plain_text": "text",
        "query": "text",
        "summary": "text",
        "report": "markdown",
        "link": "url",
        "uri": "url",
        "webpage": "url",
        "spreadsheet": "csv",
        "dataframe": "csv",
        "md": "markdown",
        "yml": "yaml",
        "json_object": "json",
        "slides": "pptx",
        "presentation": "pptx",
        "powerpoint": "pptx",
        "ppt": "pptx",
        "jpeg": "jpg",
        "source_code": "code",
        "code_file": "code",
        "kernel_code": "code",
        "operator_code": "code",
        "cpp_code": "code",
        "python_code": "code",
        "javascript_code": "code",
        "program": "code",
        "shell_script": "code",
        "chart": "png",
        "flowchart": "svg",
        "mermaid": "text",
        "paper": "pdf",
        "academic_paper": "pdf",
        "publication": "pdf",
    }

    @classmethod
    def default(cls) -> "DataTypeVocabulary":
        """Create the default DataType vocabulary with predefined terms and aliases."""
        return cls(
            version=cls.VERSION,
            vocab=cls.DEFAULT_VOCAB,
            aliases=cls.DEFAULT_ALIASES,
        )

    @classmethod
    def from_config(cls, config: Any) -> "DataTypeVocabulary":
        """Build vocabulary from NormalizationConfig.

        Uses the default vocabulary terms. If config provides custom
        aliases via data_type_aliases, they are merged with defaults.
        """
        aliases = cls.DEFAULT_ALIASES
        if hasattr(config, "data_type_aliases") and config.data_type_aliases:
            aliases = {**cls.DEFAULT_ALIASES, **config.data_type_aliases}
        return cls(
            version=cls.VERSION,
            vocab=cls.DEFAULT_VOCAB,
            aliases=aliases,
        )

    @classmethod
    def with_custom_aliases(
        cls,
        aliases: Dict[str, str],
    ) -> "DataTypeVocabulary":
        """Create a DataType vocabulary with additional custom aliases.

        Custom aliases are merged with the default aliases. If a custom alias
        conflicts with a default alias, the custom alias takes precedence.

        Args:
            aliases: Additional alias mappings to add.

        Returns:
            A new DataTypeVocabulary instance with merged aliases.
        """
        merged_aliases = {**cls.DEFAULT_ALIASES, **aliases}
        return cls(
            version=cls.VERSION,
            vocab=cls.DEFAULT_VOCAB,
            aliases=merged_aliases,
        )

    @property
    def unknown_type(self) -> str:
        """Return the unknown type marker."""
        return "unknown"

    def resolve(self, raw_token: str) -> DataTypeResolution:
        """Resolve a raw token to a canonical vocabulary term.

        Returns a DataTypeResolution with the normalized value, method,
        and confidence score.
        """
        normalized = raw_token.lower().strip()
        # Direct match in vocab
        if normalized in self.vocab:
            return DataTypeResolution(
                normalized_value=normalized,
                method="exact",
                confidence=1.0,
            )
        # Check aliases
        alias_result = self.aliases.get(normalized)
        if alias_result and alias_result in self.vocab:
            return DataTypeResolution(
                normalized_value=alias_result,
                method="alias_map",
                confidence=0.95,
            )
        # Fallback to "unknown" if available
        if "unknown" in self.vocab:
            return DataTypeResolution(
                normalized_value=None,  # Indicates unknown/fallback
                method="unknown",
                confidence=0.0,
            )
        return DataTypeResolution(
            normalized_value=None,
            method="unknown",
            confidence=0.0,
        )

    def contains(self, token: str) -> bool:
        """Check if a token is in the vocabulary (canonical or alias)."""
        return token in self.vocab or token in self.aliases
