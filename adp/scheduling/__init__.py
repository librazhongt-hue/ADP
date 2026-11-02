"""Generic in-process scheduling. See docs/10-Scheduling设计.md.

Two independent pieces:

- ``AsyncScheduler``: a minimal ``asyncio``-based recurring/one-off task
  runner. Knows nothing about loops, decisions, or triggers.
- ``TriggerScanner``: the platform-specific glue that polls
  ``DecisionRecorder.list_pending_triggers`` on a fixed interval (registered
  as one ``AsyncScheduler`` job) and invokes a callback for each due run.
  The scanner itself holds no state; on restart it just asks
  ``list_pending_triggers`` again -- see docs/08-待确认事项汇总.md (专题1).
"""

from adp.scheduling.scheduler import AsyncScheduler, ScheduledJobHandle
from adp.scheduling.trigger_scanner import TriggerScanner

__all__ = ["AsyncScheduler", "ScheduledJobHandle", "TriggerScanner"]
