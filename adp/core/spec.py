"""Graph specification types for the Loop Engine.

See docs/02-LoopEngine设计.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Literal


class StageType(str, Enum):
    SENSE = "sense"
    UNDERSTAND = "understand"
    DECIDE = "decide"
    ACT = "act"
    OBSERVE = "observe"
    LEARN = "learn"


@dataclass
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0.0


@dataclass
class NodeSpec:
    """A single executable step in a ``LoopSpec``.

    ``handler`` receives whatever the previous node produced (or the initial
    input for the entry node) plus the shared ``RunContext``, and returns its
    own output. For nodes that correspond to one of the six stages, the
    handler's signature should match the relevant Protocol in
    ``adp.core.stages``, but this is a convention, not an enforced
    constraint -- the engine only calls ``handler(node_input, ctx)``.
    """

    name: str
    handler: Callable[..., Awaitable[Any]]
    stage: StageType | None = None
    timeout_seconds: float | None = None
    retry: RetryPolicy | None = None


@dataclass
class RouteResult:
    next_nodes: list[str] | Literal["END"]
    reason: str | None = None


RouterFn = Callable[[Any, Any], Awaitable[RouteResult]]
"""``(node_output, RunContext) -> RouteResult``."""


@dataclass
class LoopSpec:
    loop_id: str
    project_id: str
    nodes: dict[str, NodeSpec]
    entry: str
    routers: dict[str, RouterFn] = field(default_factory=dict)
    interrupts: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.entry not in self.nodes:
            raise ValueError(f"entry node '{self.entry}' is not registered in nodes")
        for node_name in self.routers:
            if node_name not in self.nodes:
                raise ValueError(f"router references unknown node '{node_name}'")
