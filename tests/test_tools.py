import pytest

from adp.llm.contracts import ToolCallRequest
from adp.tools.contracts import ToolParameterSchema, ToolSpec
from adp.tools.executor import ToolExecutor
from adp.tools.registry import ToolRegistry


async def _add(a: int, b: int) -> int:
    return a + b


async def _always_fails() -> None:
    raise RuntimeError("boom")


async def _never_returns() -> None:
    import asyncio

    await asyncio.sleep(10)


class FakeDynamicSource:
    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs = specs

    async def list_tools(self) -> list[ToolSpec]:
        return self._specs


def _spec(name: str, handler, category: str | None = None, timeout_seconds: float | None = None) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"test tool {name}",
        parameters=ToolParameterSchema(json_schema={"type": "object", "properties": {}}),
        category=category,
        handler=handler,
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_registry_static_and_dynamic_sources_merge():
    registry = ToolRegistry()
    registry.register(_spec("add", _add, category="math"))
    registry.add_source(FakeDynamicSource([_spec("noop", None, category="misc")]))

    all_tools = await registry.list()
    assert {t.name for t in all_tools} == {"add", "noop"}

    math_tools = await registry.list(category="math")
    assert [t.name for t in math_tools] == ["add"]

    schemas = await registry.to_llm_schemas()
    assert {s.name for s in schemas} == {"add", "noop"}


@pytest.mark.asyncio
async def test_executor_executes_registered_tool_successfully():
    registry = ToolRegistry()
    registry.register(_spec("add", _add))
    executor = ToolExecutor(registry)

    result = await executor.execute("add", {"a": 1, "b": 2}, call_id="c-1")

    assert result.status == "success"
    assert result.output == 3


@pytest.mark.asyncio
async def test_executor_reports_missing_tool():
    registry = ToolRegistry()
    executor = ToolExecutor(registry)

    result = await executor.execute("missing", {}, call_id="c-2")

    assert result.status == "failed"
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_executor_reports_tool_error():
    registry = ToolRegistry()
    registry.register(_spec("boom", _always_fails))
    executor = ToolExecutor(registry)

    result = await executor.execute("boom", {}, call_id="c-3")

    assert result.status == "failed"
    assert "boom" in result.error


@pytest.mark.asyncio
async def test_executor_reports_timeout():
    registry = ToolRegistry()
    registry.register(_spec("slow", _never_returns, timeout_seconds=0.01))
    executor = ToolExecutor(registry)

    result = await executor.execute("slow", {}, call_id="c-4")

    assert result.status == "timeout"


@pytest.mark.asyncio
async def test_executor_batch_runs_concurrently():
    registry = ToolRegistry()
    registry.register(_spec("add", _add))
    executor = ToolExecutor(registry)

    calls = [
        ToolCallRequest(tool_name="add", arguments={"a": 1, "b": 1}, call_id="b-1"),
        ToolCallRequest(tool_name="add", arguments={"a": 2, "b": 2}, call_id="b-2"),
    ]
    results = await executor.execute_batch(calls)

    assert [r.output for r in results] == [2, 4]


@pytest.mark.asyncio
async def test_executor_respects_permission_checker():
    class DenyAll:
        async def allow(self, tool_name, arguments, call_id) -> bool:
            return False

    registry = ToolRegistry()
    registry.register(_spec("add", _add))
    executor = ToolExecutor(registry, permission_checker=DenyAll())

    result = await executor.execute("add", {"a": 1, "b": 1}, call_id="c-5")

    assert result.status == "failed"
    assert result.error == "permission denied"
