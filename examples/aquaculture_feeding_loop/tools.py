"""A fake device tool standing in for the real ``deviceagent`` HTTP call
(see ``backend/services/work_task_service.py`` triggering deviceagent's
``/api/v1/chat``). Registered via ``adp.tools.ToolRegistry`` so the Act
stage exercises the real tool-execution path, not just a direct call.
"""

from __future__ import annotations

from adp.tools.contracts import ToolParameterSchema, ToolSpec


def trigger_feeder_tool_spec(call_log: list[dict]) -> ToolSpec:
    """Builds a ``trigger_feeder`` tool bound to the given ``call_log``.

    Each environment built by ``loop.build_feeding_loop_environment`` gets
    its own ``call_log`` list, so independent environments (e.g. one per
    test) never see each other's calls.
    """

    async def trigger_feeder(pond_id: str, amount_kg: float) -> dict:
        """Simulates dispatching a feed command to the device execution layer."""

        call_log.append({"pond_id": pond_id, "amount_kg": amount_kg})
        return {"pond_id": pond_id, "dispensed_kg": amount_kg, "device": "feeder-sim-01"}

    return ToolSpec(
        name="trigger_feeder",
        description="Dispatch a feeding command to the pond's feeder device.",
        parameters=ToolParameterSchema(
            json_schema={
                "type": "object",
                "properties": {
                    "pond_id": {"type": "string"},
                    "amount_kg": {"type": "number"},
                },
                "required": ["pond_id", "amount_kg"],
            }
        ),
        handler=trigger_feeder,
        timeout_seconds=5.0,
    )
