"""Prompt loading and rendering. See docs/06-LLMGateway设计.md §4.3.

Prompt *content* is always project-specific (see docs/00 §3.1) -- this
module only provides the loading/rendering mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol


@dataclass
class PromptTemplate:
    template_id: str
    content: str
    source: Literal["file", "db"]
    version: str | None = None


class PromptSource(Protocol):
    async def load(self, template_id: str) -> PromptTemplate | None: ...


class FilePromptSource:
    """Loads ``<directory>/<template_id>.md`` files, mirroring
    ``agent_new/prompts/__init__.py``'s ``load_prompt()`` convention.
    """

    def __init__(self, directory: str | Path, extension: str = ".md") -> None:
        self._directory = Path(directory)
        self._extension = extension

    async def load(self, template_id: str) -> PromptTemplate | None:
        path = self._directory / f"{template_id}{self._extension}"
        if not path.exists():
            return None
        return PromptTemplate(template_id=template_id, content=path.read_text(encoding="utf-8"), source="file")


class PromptRegistry:
    """Tries each source in order and returns the first hit; the ordering
    (file-first vs DB-first) is a deployment decision left to the reference
    implementation, not something the platform enforces.
    """

    def __init__(self, sources: list[PromptSource]) -> None:
        self._sources = sources
        self._cache: dict[str, PromptTemplate] = {}

    async def get(self, template_id: str) -> PromptTemplate:
        if template_id in self._cache:
            return self._cache[template_id]

        for source in self._sources:
            template = await source.load(template_id)
            if template is not None:
                self._cache[template_id] = template
                return template

        raise KeyError(f"prompt template '{template_id}' not found in any registered source")

    async def render(self, template_id: str, variables: dict[str, Any] | None = None) -> str:
        template = await self.get(template_id)
        return template.content.format(**(variables or {}))
