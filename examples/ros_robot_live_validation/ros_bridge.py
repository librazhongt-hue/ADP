"""Act-side bridge to ROS: dry-run by default; optional real Nav2 / VLM scripts.

Safety model mirrors aquaculture live validation:
- Default ``dry_run=True``: never talks to ROS, only records the would-be command.
- ``dry_run=False`` (``--live``):
  - ``goto`` → ``go_waypoint_test.py``
  - ``vlm_detect_once`` → ``vlm_detect_once_test.py`` (car still)
  - ``vlm_object_search`` → ``vlm_object_search_test.py`` (needs wander/nav stack)
  - ``announce_found`` / ``report_miss`` stay log-only (Understand already ran VLM)
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RosActLogEntry:
    action: str
    params: dict[str, Any]
    dry_run: bool
    status: str
    detail: str


@dataclass
class RosBridge:
    dry_run: bool = True
    ros_ws: Path | None = None
    call_log: list[RosActLogEntry] = field(default_factory=list)

    def _python_bin(self) -> str:
        return (
            os.environ.get("ADP_ROS_PYTHON")
            or os.environ.get("ROS_PYTHON")
            or ("/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable)
        )

    async def dispatch(self, action: str, params: dict[str, Any]) -> RosActLogEntry:
        soft = {
            "chat",
            "stop",
            "exit",
            "unsupported",
            "qa_nod",
            "announce_found",
            "report_miss",
        }
        if self.dry_run or action in soft:
            entry = RosActLogEntry(
                action=action,
                params=params,
                dry_run=True,
                status="success",
                detail=f"[dry-run] would dispatch action={action} params={params}",
            )
            self.call_log.append(entry)
            return entry

        if action == "goto":
            return await self._run_script(
                "goto",
                params,
                relative=("src", "test", "go_waypoint_test.py"),
                argv=["--name", str(params.get("place") or "")],
                extra_flags=["--ignore-yaw"] if params.get("ignore_yaw") else [],
            )
        if action == "vlm_detect_once":
            target = str(params.get("target") or "")
            return await self._run_script(
                "vlm_detect_once",
                params,
                relative=("src", "test", "vlm_detect_once_test.py"),
                argv=["--target", target, "--no-speak", "--no-beep"],
                ok_codes={0, 2},  # FOUND or NOT_FOUND both mean script ran
            )
        if action == "vlm_object_search":
            target = str(params.get("target") or "")
            motion = str(params.get("motion") or "wander")
            if motion not in {"wander", "nav"}:
                motion = "wander"
            return await self._run_script(
                "vlm_object_search",
                params,
                relative=("src", "test", "vlm_object_search_test.py"),
                argv=["--target", target, "--motion", motion, "--confirm-hits", "1", "--no-speak"],
                ok_codes={0, 2},
            )
        if action in {"drive", "arm_routine", "search"}:
            entry = RosActLogEntry(
                action=action,
                params=params,
                dry_run=False,
                status="skipped",
                detail=(
                    f"[safety] real Act for action={action} is not enabled; "
                    "use scenario c for VLM search, or dry-run"
                ),
            )
            self.call_log.append(entry)
            return entry

        entry = RosActLogEntry(
            action=action,
            params=params,
            dry_run=False,
            status="failed",
            detail=f"unknown action: {action}",
        )
        self.call_log.append(entry)
        return entry

    async def _run_script(
        self,
        action: str,
        params: dict[str, Any],
        *,
        relative: tuple[str, ...],
        argv: list[str],
        extra_flags: list[str] | None = None,
        ok_codes: set[int] | None = None,
    ) -> RosActLogEntry:
        ok_codes = ok_codes or {0}
        if self.ros_ws is None or not self.ros_ws.exists():
            entry = RosActLogEntry(
                action=action,
                params=params,
                dry_run=False,
                status="failed",
                detail="ADP_ROS_WS path missing or not found",
            )
            self.call_log.append(entry)
            return entry
        script = self.ros_ws.joinpath(*relative)
        if not script.exists():
            entry = RosActLogEntry(
                action=action,
                params=params,
                dry_run=False,
                status="failed",
                detail=f"script not found: {script}",
            )
            self.call_log.append(entry)
            return entry

        cmd = [self._python_bin(), str(script), *argv, *(extra_flags or [])]
        env = os.environ.copy()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(script.parent),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout_b, _ = await proc.communicate()
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        code = proc.returncode if proc.returncode is not None else -1
        ok = code in ok_codes
        detail = f"exit={code}\n{(stdout[-2000:] if stdout else '')}".strip()
        entry = RosActLogEntry(
            action=action,
            params={**params, "exit_code": code},
            dry_run=False,
            status="success" if ok else "failed",
            detail=detail,
        )
        self.call_log.append(entry)
        return entry
