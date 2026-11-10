"""
Application configuration.

Two sources, kept deliberately separate:

1. **Environment settings** (`Settings`) - secrets, URLs and mode switches.
   Loaded from environment variables / `.env`, prefixed `SN_`.
   These differ per deployment (dev / test / prod).

2. **Business rules** (`load_yaml`) - plans, planner rules, quality thresholds,
   LLM model choices. Loaded from `backend/config/*.yaml`.
   These are the same in every deployment and are reviewed like code.

Nothing else in the codebase should read `os.environ` directly - import
`get_settings()` instead, so every setting is documented in one place.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ directory (this file is backend/app/core/config.py)
BACKEND_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BACKEND_DIR / "config"


class Settings(BaseSettings):
    """Every environment-level setting the system reads. See `.env.example`."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_prefix="SN_", extra="ignore"
    )

    env: Literal["dev", "test", "prod"] = "dev"

    # --- Data tier: three Postgres roles (see migrations/000_roles.sql) ---
    database_owner_url: str = "postgresql+psycopg://sn_owner:sn_owner_dev_pw@localhost:5432/sales_navigator"
    database_url: str = "postgresql+psycopg://sn_app:sn_app_dev_pw@localhost:5432/sales_navigator"
    database_system_url: str = "postgresql+psycopg://sn_system:sn_system_dev_pw@localhost:5432/sales_navigator"

    # --- Secrets ---
    jwt_secret: str = "dev-only-jwt-secret-change-me-0123456789abcdef"
    jwt_access_minutes: int = 60
    # 32-byte urlsafe-base64 AES key. Empty -> derived from jwt_secret (dev only).
    encryption_key: str = ""
    hmac_pepper: str = "dev-only-pepper"

    # --- Mode switches ---
    data_mode: Literal["demo", "live"] = "demo"
    llm_mode: Literal["offline", "claude"] = "offline"
    verifier_mode: Literal["fake", "smtp"] = "fake"
    brave_api_key: str = ""
    # The Anthropic SDK reads ANTHROPIC_API_KEY itself; we only check it exists.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # --- Money / guardrails ---
    fx_usd_inr: float = 93.0
    platform_daily_spend_cap_inr: float = 5000.0

    # --- Presentation tier ---
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton. Tests override via environment variables."""
    return Settings()


@lru_cache
def load_yaml(name: str) -> dict[str, Any]:
    """
    Load a business-rule file from backend/config/<name>.yaml.

    Cached for the life of the process: rule changes ship as a deploy,
    exactly like code changes, so they are reviewed and tested.
    """
    path = CONFIG_DIR / f"{name}.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
