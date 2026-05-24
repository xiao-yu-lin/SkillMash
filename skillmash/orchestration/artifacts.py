"""Load graph build artifacts for Skill orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BuildArtifacts:
    """Offline build artifacts needed by orchestration."""

    build_dir: Path
    manifest: dict[str, Any]
    skills: list[dict[str, Any]]
    graph: dict[str, Any]
    index: dict[str, Any]
    io_name_vocab: dict[str, Any] | None = None
    task_vocab: dict[str, Any] | None = None
    slot_taxonomy: dict[str, Any] | None = None
    slot_contracts: dict[str, Any] | None = None

    @property
    def skill_by_id(self) -> dict[str, dict[str, Any]]:
        return {skill["id"]: skill for skill in self.skills if skill.get("id")}


def load_build_artifacts(build_dir: str | Path) -> BuildArtifacts:
    """Load graph build artifacts through build_manifest.json."""

    root = Path(build_dir).resolve()
    manifest = _read_json(root / "build_manifest.json")
    artifacts = manifest.get("artifacts", {})
    skills_payload = _read_json(root / artifacts.get("skills", "skills.json"))
    graph = _read_json(root / artifacts.get("graph", "skill_graph.json"))
    index = _read_json(root / artifacts.get("index", "skill_index.json"))
    io_name_vocab_path = root / artifacts.get("io_name_vocab", "io_name_vocab.json")
    task_vocab_path = root / artifacts.get("task_vocab", "task_vocab.json")
    slot_taxonomy_path = root / artifacts.get("slot_taxonomy", "slot_taxonomy.json")
    slot_contracts_path = root / artifacts.get("slot_contracts", "slot_contracts.json")
    return BuildArtifacts(
        build_dir=root,
        manifest=manifest,
        skills=skills_payload.get("skills", []),
        graph=graph,
        index=index,
        io_name_vocab=_read_optional_json(io_name_vocab_path),
        task_vocab=_read_optional_json(task_vocab_path),
        slot_taxonomy=_read_optional_json(slot_taxonomy_path),
        slot_contracts=_read_optional_json(slot_contracts_path),
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing build artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
