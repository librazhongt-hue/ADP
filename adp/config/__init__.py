"""Layered platform configuration. See docs/09-Config设计.md.

``PlatformSettings`` only carries infrastructure-level fields (database,
LLM provider, scheduling defaults). Reference implementations subclass it to
add their own settings groups -- this subclassing *is* the extension
mechanism; the platform does not otherwise special-case business config.
"""

from adp.config.settings import DatabaseSettings, LLMSettings, PlatformSettings, SchedulingSettings

__all__ = [
    "DatabaseSettings",
    "LLMSettings",
    "SchedulingSettings",
    "PlatformSettings",
]
