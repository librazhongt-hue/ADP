"""Protocols for the six stages.

A reference implementation does not need to subclass anything here; any
object whose methods match these signatures (structural typing) can be
registered as a node via ``adp.core.registry.LoopRegistry``.
"""

from __future__ import annotations

from typing import Protocol

from adp.core.contracts import (
    ActResult,
    Context,
    Decision,
    LearnSignal,
    ObservationRecord,
    RunContext,
    SenseEvent,
)


class SenseStage(Protocol):
    async def sense(self, ctx: RunContext) -> list[SenseEvent]: ...


class UnderstandStage(Protocol):
    async def understand(self, events: list[SenseEvent], ctx: RunContext) -> Context: ...


class DecideStage(Protocol):
    async def decide(self, context: Context, ctx: RunContext) -> Decision: ...


class ActStage(Protocol):
    async def act(self, decision: Decision, ctx: RunContext) -> ActResult: ...


class ObserveStage(Protocol):
    async def observe(self, act_result: ActResult, ctx: RunContext) -> ObservationRecord: ...


class LearnStage(Protocol):
    async def learn(self, observation: ObservationRecord, ctx: RunContext) -> LearnSignal: ...
