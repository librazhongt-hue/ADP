"""Platform settings groups. See docs/09-Config设计.md §2-3.

Each settings group is its own ``BaseSettings`` subclass with its own
``env_prefix``, loaded independently. ``PlatformSettings`` just composes
them. Reference implementations extend this by subclassing
``PlatformSettings`` and adding further groups of their own (see
docs/09 §4 for a worked example) -- no platform code branches on
project-specific fields.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADP_DB_", env_file=".env", extra="ignore")

    dsn: str = "sqlite+aiosqlite:///./adp.db"
    """SQLAlchemy-style async DSN. Defaults to a local SQLite file so the
    platform is usable without any external database for development/tests.
    """


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADP_LLM_", env_file=".env", extra="ignore")

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    default_model: str = "gpt-4o-mini"


class SchedulingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADP_SCHEDULING_", env_file=".env", extra="ignore")

    poll_interval_seconds: float = 30.0
    """How often the platform's ``AsyncScheduler`` polls for due work.

    See docs/10-Scheduling设计.md -- this is the *only* scheduling-related
    platform default; anything about "when should the next decision run"
    is a Decision Record concern (``NextTriggerPolicy``), not this setting.
    """


class PlatformSettings(BaseSettings):
    """Root settings object. Subclass this to add project-specific settings
    groups; e.g.::

        class FeedingSettings(BaseSettings):
            model_config = SettingsConfigDict(env_prefix="AQUA_FEEDING_")
            risk_interval_minutes: dict[str, int] = {}

        class AquacultureSettings(PlatformSettings):
            feeding: FeedingSettings = Field(default_factory=FeedingSettings)
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    scheduling: SchedulingSettings = Field(default_factory=SchedulingSettings)
