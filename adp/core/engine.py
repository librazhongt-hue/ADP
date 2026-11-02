"""Minimal default Loop Engine runtime.

This is intentionally a simple implementation meant to validate the
``LoopSpec`` contract end-to-end (see docs/02-LoopEngine设计.md). Persistence
of ``LoopRun`` is delegated to a pluggable ``LoopRunStore`` (defaults to an
in-memory one); distributed execution and sophisticated scheduling remain
out of scope for this skeleton.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from adp.core.contracts import RunContext
from adp.core.run import LoopRun
from adp.core.spec import LoopSpec, NodeSpec, RouteResult
from adp.core.store import InMemoryLoopRunStore, LoopRunStore

logger = logging.getLogger(__name__)

NodeHook = Callable[[str, Any, RunContext], Awaitable[None]]
LoopCompleteHook = Callable[[LoopRun], Awaitable[None]]


class LoopEngineError(RuntimeError):
    pass


class LoopEngine:
    """Executes a ``LoopSpec`` starting from its entry node.

    Node outputs accumulate in ``LoopRun.state`` keyed by node name, so any
    later node/router can look back at earlier outputs via the ``RunContext``
    (passed alongside the immediate predecessor's output).
    """

    def __init__(self, store: LoopRunStore | None = None) -> None:
        self._store = store or InMemoryLoopRunStore()
        self._on_node_start: list[NodeHook] = []
        self._on_node_end: list[NodeHook] = []
        self._on_loop_complete: list[LoopCompleteHook] = []

    def on_node_start(self, hook: NodeHook) -> None:
        self._on_node_start.append(hook)

    def on_node_end(self, hook: NodeHook) -> None:
        self._on_node_end.append(hook)

    def on_loop_complete(self, hook: LoopCompleteHook) -> None:
        self._on_loop_complete.append(hook)

    async def start(self, loop_spec: LoopSpec, initial_input: Any, ctx: RunContext) -> LoopRun:
        run = LoopRun(
            trace_id=ctx.trace_id,
            loop_id=loop_spec.loop_id,
            project_id=loop_spec.project_id,
            status="running",
            current_node=loop_spec.entry,
            started_at=datetime.now(timezone.utc),
        )
        await self._store.save(run)
        await self._run_from(loop_spec, run, ctx, node_name=loop_spec.entry, node_input=initial_input)
        return run

    async def resume(self, loop_spec: LoopSpec, trace_id: str, ctx: RunContext, event: Any) -> LoopRun:
        run = await self._store.get(trace_id)
        if run is None:
            raise LoopEngineError(f"no run found for trace_id={trace_id}")
        if run.status != "waiting_approval" or run.pending_node is None:
            raise LoopEngineError(f"run {trace_id} is not waiting for approval (status={run.status})")

        node_name = run.pending_node
        run.status = "running"
        run.pending_node = None
        await self._store.save(run)
        await self._advance(loop_spec, run, ctx, node_name=node_name, node_output=event)
        return run

    async def _run_from(
        self, loop_spec: LoopSpec, run: LoopRun, ctx: RunContext, node_name: str, node_input: Any
    ) -> None:
        output = await self._execute_node(run, loop_spec.nodes[node_name], node_input, ctx)
        if output is None and run.status == "failed":
            await self._store.save(run)
            return
        run.state[node_name] = output

        if node_name in loop_spec.interrupts and getattr(output, "requires_approval", False):
            run.status = "waiting_approval"
            run.current_node = node_name
            run.pending_node = node_name
            await self._store.save(run)
            return

        await self._store.save(run)
        await self._advance(loop_spec, run, ctx, node_name=node_name, node_output=output)

    async def _advance(
        self, loop_spec: LoopSpec, run: LoopRun, ctx: RunContext, node_name: str, node_output: Any
    ) -> None:
        router = loop_spec.routers.get(node_name)
        if router is None:
            run.status = "completed"
            run.current_node = None
            await self._store.save(run)
            await self._fire_loop_complete(run)
            return

        route: RouteResult = await router(node_output, ctx)
        if route.next_nodes == "END":
            run.status = "completed"
            run.current_node = None
            await self._store.save(run)
            await self._fire_loop_complete(run)
            return

        for next_name in route.next_nodes:
            run.current_node = next_name
            await self._run_from(loop_spec, run, ctx, node_name=next_name, node_input=node_output)
            if run.status in ("waiting_approval", "failed"):
                return

    async def _execute_node(self, run: LoopRun, node: NodeSpec, node_input: Any, ctx: RunContext) -> Any:
        await self._fire_node_start(node.name, node_input, ctx)

        max_attempts = node.retry.max_attempts if node.retry else 1
        backoff = node.retry.backoff_seconds if node.retry else 0.0
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                coro = node.handler(node_input, ctx)
                if node.timeout_seconds is not None:
                    output = await asyncio.wait_for(coro, timeout=node.timeout_seconds)
                else:
                    output = await coro
                await self._fire_node_end(node.name, output, ctx)
                return output
            except Exception as exc:  # noqa: BLE001 - intentionally broad for a generic engine
                last_error = exc
                logger.warning("node '%s' attempt %d/%d failed: %s", node.name, attempt, max_attempts, exc)
                if attempt < max_attempts and backoff > 0:
                    await asyncio.sleep(backoff)

        run.status = "failed"
        run.current_node = node.name
        run.error = str(last_error)
        return None

    async def _fire_node_start(self, node_name: str, node_input: Any, ctx: RunContext) -> None:
        for hook in self._on_node_start:
            await hook(node_name, node_input, ctx)

    async def _fire_node_end(self, node_name: str, node_output: Any, ctx: RunContext) -> None:
        for hook in self._on_node_end:
            await hook(node_name, node_output, ctx)

    async def _fire_loop_complete(self, run: LoopRun) -> None:
        for hook in self._on_loop_complete:
            await hook(run)

    async def get_run(self, trace_id: str) -> LoopRun | None:
        return await self._store.get(trace_id)
