"""Tool registration: static (in-code) and dynamic (e.g. DB-driven) sources
co-exist. See docs/07-ToolFramework设计.md §4.1.
"""

from __future__ import annotations

from typing import Protocol

from adp.llm.contracts import ToolSchema
from adp.tools.contracts import ToolSpec


class ToolSource(Protocol):
    async def list_tools(self) -> list[ToolSpec]: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._static_tools: dict[str, ToolSpec] = {}
        self._sources: list[ToolSource] = []

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._static_tools:
            raise ValueError(f"tool '{spec.name}' already registered")
        self._static_tools[spec.name] = spec

    def add_source(self, source: ToolSource) -> None:
        self._sources.append(source)

    async def get(self, name: str) -> ToolSpec | None:
        if name in self._static_tools:
            return self._static_tools[name]
        for source in self._sources:
            for spec in await source.list_tools():
                if spec.name == name:
                    return spec
        return None

    async def list(self, category: str | None = None) -> list[ToolSpec]:
        all_specs = list(self._static_tools.values())
        for source in self._sources:
            all_specs.extend(await source.list_tools())
        if category is None:
            return all_specs
        return [spec for spec in all_specs if spec.category == category]

    async def to_llm_schemas(self, category: str | None = None) -> list[ToolSchema]:
        specs = await self.list(category)
        return [
            ToolSchema(name=spec.name, description=spec.description, parameters=spec.parameters.json_schema)
            for spec in specs
        ]
