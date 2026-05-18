"""Skill folder discovery."""

from __future__ import annotations

from pathlib import Path

from skillmash.representation.models import SkillFolder


class SkillFolderScanner:
    """Find folders that contain a SKILL.md entrypoint."""

    def scan(self, skills_root: Path | str) -> list[SkillFolder]:
        root = Path(skills_root).resolve()
        if not root.exists():
            raise FileNotFoundError(f"skills_root does not exist: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"skills_root is not a directory: {root}")

        folders: list[SkillFolder] = []
        for entry in sorted(root.rglob("SKILL.md"), key=lambda path: path.as_posix().lower()):
            folder_path = entry.parent
            relative_path = folder_path.relative_to(root).as_posix()
            folders.append(
                SkillFolder(
                    id_hint=folder_path.name,
                    path=folder_path,
                    entry=entry,
                    relative_path=relative_path,
                )
            )
        return folders
