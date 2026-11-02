"""Plugin registration helper for building a ``LoopSpec``.

See docs/02-LoopEngine设计.md §4.
"""

from __future__ import annotations

from adp.core.spec import LoopSpec, NodeSpec, RouteResult, RouterFn


def _constant_router(next_nodes: list[str]) -> RouterFn:
    async def _router(_node_output, _ctx) -> RouteResult:
        return RouteResult(next_nodes=next_nodes, reason="static edge")

    return _router


class LoopRegistry:
    """Accumulates ``NodeSpec``s and compiles them into a ``LoopSpec``.

    Reference implementations register their Sense/Understand/Decide/Act/
    Observe/Learn (or custom) node implementations here, then describe the
    graph topology as a plain list of static edges (auto-compiled into
    constant routers) and/or explicit dynamic routers for branching nodes.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, NodeSpec] = {}

    def register_node(self, spec: NodeSpec) -> None:
        if spec.name in self._nodes:
            raise ValueError(f"node '{spec.name}' already registered")
        self._nodes[spec.name] = spec

    def build_loop(
        self,
        loop_id: str,
        project_id: str,
        entry: str,
        edges: list[tuple[str, str]] | None = None,
        dynamic_routers: dict[str, RouterFn] | None = None,
        interrupts: set[str] | None = None,
    ) -> LoopSpec:
        """Compile registered nodes into a ``LoopSpec``.

        ``edges`` is a list of ``(from_node, to_node)`` static edges; nodes
        with multiple outgoing static edges are treated as parallel fan-out
        (all targets execute). ``dynamic_routers`` overrides/adds routers for
        nodes that need conditional branching instead of a fixed target.
        """

        static_targets: dict[str, list[str]] = {}
        for source, target in edges or []:
            static_targets.setdefault(source, []).append(target)

        routers: dict[str, RouterFn] = {
            name: _constant_router(targets) for name, targets in static_targets.items()
        }
        routers.update(dynamic_routers or {})

        return LoopSpec(
            loop_id=loop_id,
            project_id=project_id,
            nodes=dict(self._nodes),
            entry=entry,
            routers=routers,
            interrupts=interrupts or set(),
        )
