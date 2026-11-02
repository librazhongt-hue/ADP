import os

import pytest
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from adp.config import PlatformSettings


def test_platform_settings_defaults(tmp_path, monkeypatch):
    # Each nested settings group (``DatabaseSettings``/``LLMSettings``/...)
    # hardcodes ``env_file=".env"`` and is built via its own
    # ``default_factory`` -- so it re-reads ``.env`` from the *current
    # working directory* independently, regardless of any ``_env_file``
    # override passed to ``PlatformSettings`` itself. Running from an empty
    # ``tmp_path`` is what actually isolates this test from a developer's
    # real local ``.env`` (e.g. examples/aquaculture_live_validation's real
    # DB/LLM credentials).
    monkeypatch.chdir(tmp_path)
    settings = PlatformSettings()
    assert settings.database.dsn == "sqlite+aiosqlite:///./adp.db"
    assert settings.llm.default_model == "gpt-4o-mini"
    assert settings.scheduling.poll_interval_seconds == 30.0


def test_platform_settings_reads_env_vars(monkeypatch):
    monkeypatch.setenv("ADP_DB_DSN", "sqlite+aiosqlite:///custom.db")
    monkeypatch.setenv("ADP_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("ADP_SCHEDULING_POLL_INTERVAL_SECONDS", "5")

    settings = PlatformSettings()

    assert settings.database.dsn == "sqlite+aiosqlite:///custom.db"
    assert settings.llm.api_key == "sk-test"
    assert settings.scheduling.poll_interval_seconds == 5.0


def test_reference_implementation_can_extend_platform_settings(monkeypatch):
    """Demonstrates the extension mechanism from docs/09-Config设计.md: a
    reference implementation subclasses ``PlatformSettings`` and adds its
    own settings group, without the platform knowing about it.
    """

    class FeedingSettings(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="AQUA_FEEDING_", extra="ignore")
        default_rate_pct: float = 0.05

    class AquacultureSettings(PlatformSettings):
        feeding: FeedingSettings = Field(default_factory=FeedingSettings)

    monkeypatch.setenv("AQUA_FEEDING_DEFAULT_RATE_PCT", "0.08")
    monkeypatch.setenv("ADP_LLM_DEFAULT_MODEL", "gpt-4o")

    settings = AquacultureSettings()

    assert settings.feeding.default_rate_pct == 0.08
    assert settings.llm.default_model == "gpt-4o"  # platform field still works
