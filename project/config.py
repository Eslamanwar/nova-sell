"""Centralized configuration for NovaSell agent.

All environment variables and settings are managed here using pydantic-settings.
"""
from __future__ import annotations

import os
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class NovaModelConfig(BaseSettings):
    """AWS Nova model configuration."""

    # Nova model identifiers (via LiteLLM / OpenRouter)
    nova_lite_model: str = Field(default="amazon/nova-lite-v1:0", alias="NOVA_LITE_MODEL")
    nova_pro_model: str = Field(default="amazon/nova-pro-v1:0", alias="NOVA_PRO_MODEL")
    nova_sonic_model: str = Field(default="amazon/nova-sonic-v1:0", alias="NOVA_SONIC_MODEL")

    # Nova Act browser automation
    nova_act_workflow_definition: str = Field(default="novasell", alias="NOVA_ACT_WORKFLOW_DEFINITION")
    nova_act_model_id: str = Field(default="nova-act-latest", alias="NOVA_ACT_MODEL_ID")

    # LLM gateway
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENAI_BASE_URL"
    )

    # AWS credentials
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_access_key_id: str = Field(default="", alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str = Field(default="", alias="AWS_SECRET_ACCESS_KEY")

    # Nova Sonic WebSocket endpoint
    nova_sonic_endpoint: str = Field(
        default="wss://nova-sonic.{region}.amazonaws.com",
        alias="NOVA_SONIC_ENDPOINT",
    )

    class Config:
        env_file = ".env"
        populate_by_name = True


class DubizzleConfig(BaseSettings):
    """Dubizzle marketplace configuration."""

    dubizzle_email: str = Field(default="", alias="DUBIZZLE_EMAIL")
    dubizzle_password: str = Field(default="", alias="DUBIZZLE_PASS")
    dubizzle_base_url: str = Field(
        default="https://dubai.dubizzle.com", alias="DUBIZZLE_BASE_URL"
    )
    dubizzle_place_ad_url: str = Field(
        default="https://dubai.dubizzle.com/place-your-ad/",
        alias="DUBIZZLE_PLACE_AD_URL",
    )
    dubizzle_login_url: str = Field(
        default="https://dubai.dubizzle.com/login/", alias="DUBIZZLE_LOGIN_URL"
    )
    dubizzle_chat_url: str = Field(
        default="https://dubai.dubizzle.com/chat/", alias="DUBIZZLE_CHAT_URL"
    )
    default_location: str = Field(default="Dubai", alias="DUBIZZLE_DEFAULT_LOCATION")

    class Config:
        env_file = ".env"
        populate_by_name = True


class AntiBanConfig(BaseSettings):
    """Anti-ban and rate limiting configuration."""

    # Delay ranges (seconds)
    min_action_delay: float = Field(default=1.0, alias="MIN_ACTION_DELAY")
    max_action_delay: float = Field(default=3.0, alias="MAX_ACTION_DELAY")
    min_typing_delay: float = Field(default=0.05, alias="MIN_TYPING_DELAY")
    max_typing_delay: float = Field(default=0.15, alias="MAX_TYPING_DELAY")
    page_load_wait: float = Field(default=2.0, alias="PAGE_LOAD_WAIT")

    # Rate limiting
    max_listings_per_hour: int = Field(default=3, alias="MAX_LISTINGS_PER_HOUR")
    max_listings_per_day: int = Field(default=10, alias="MAX_LISTINGS_PER_DAY")
    max_messages_per_minute: int = Field(default=5, alias="MAX_MESSAGES_PER_MINUTE")
    cooldown_between_listings_seconds: int = Field(
        default=600, alias="COOLDOWN_BETWEEN_LISTINGS"
    )

    # Session management
    session_reuse_enabled: bool = Field(default=True, alias="SESSION_REUSE_ENABLED")
    user_data_dir: str = Field(
        default="/data/novasell/nova-act-profile", alias="NOVA_ACT_USER_DATA_DIR"
    )

    # Browser fingerprint
    viewport_width: int = Field(default=1280, alias="VIEWPORT_WIDTH")
    viewport_height: int = Field(default=720, alias="VIEWPORT_HEIGHT")
    user_agent: str = Field(default="", alias="CUSTOM_USER_AGENT")

    class Config:
        env_file = ".env"
        populate_by_name = True


class StorageConfig(BaseSettings):
    """Storage configuration for images, state, and data."""

    image_storage_dir: str = Field(
        default="/data/novasell/images", alias="IMAGE_STORAGE_DIR"
    )
    s3_bucket_name: str = Field(default="", alias="S3_BUCKET_NAME")

    # Redis for caching and rate limiting
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # PostgreSQL for persistent state
    postgres_url: str = Field(
        default="postgresql+asyncpg://novasell:novasell@localhost:5432/novasell",
        alias="DATABASE_URL",
    )

    class Config:
        env_file = ".env"
        populate_by_name = True


class NotificationConfig(BaseSettings):
    """Notification service configuration."""

    # Slack notifications
    slack_webhook_url: str = Field(default="", alias="SLACK_WEBHOOK_URL")
    slack_channel: str = Field(default="#novasell-alerts", alias="SLACK_CHANNEL")

    # Email notifications
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    notification_email: str = Field(default="", alias="NOTIFICATION_EMAIL")

    # HITL notification
    hitl_notification_enabled: bool = Field(
        default=True, alias="HITL_NOTIFICATION_ENABLED"
    )

    class Config:
        env_file = ".env"
        populate_by_name = True


class ShozonConfig(BaseSettings):
    """Shozon marketplace configuration."""

    shozon_email: str = Field(default="", alias="SHOZON_EMAIL")
    shozon_password: str = Field(default="", alias="SHOZON_PASS")
    shozon_phone: str = Field(default="", alias="SHOZON_PHONE")
    shozon_base_url: str = Field(default="https://shozon.com/", alias="SHOZON_BASE_URL")

    class Config:
        env_file = ".env"
        populate_by_name = True


class FacebookConfig(BaseSettings):
    """Facebook Marketplace configuration."""

    facebook_email: str = Field(default="", alias="FACEBOOK_EMAIL")
    facebook_password: str = Field(default="", alias="FACEBOOK_PASS")
    facebook_2fa_secret: str = Field(default="", alias="FACEBOOK_2FA_SECRET")
    capsolver_api_key: str = Field(default="", alias="CAPSOLVER_API_KEY")
    facebook_login_url: str = Field(
        default="https://www.facebook.com/login", alias="FACEBOOK_LOGIN_URL"
    )
    facebook_marketplace_create_url: str = Field(
        default="https://www.facebook.com/marketplace/create/item",
        alias="FACEBOOK_MARKETPLACE_CREATE_URL",
    )

    class Config:
        env_file = ".env"
        populate_by_name = True


class NovaSellConfig(BaseSettings):
    """Root configuration aggregating all sub-configs."""

    # Access control
    allowed_emails: str = Field(default="", alias="ALLOWED_EMAILS")

    # Temporal
    temporal_address: str = Field(
        default="localhost:7233", alias="TEMPORAL_ADDRESS"
    )

    # Sub-configurations (loaded separately)
    nova: NovaModelConfig = Field(default_factory=NovaModelConfig)
    dubizzle: DubizzleConfig = Field(default_factory=DubizzleConfig)
    shozon: ShozonConfig = Field(default_factory=ShozonConfig)
    facebook: FacebookConfig = Field(default_factory=FacebookConfig)
    anti_ban: AntiBanConfig = Field(default_factory=AntiBanConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)

    @property
    def allowed_email_list(self) -> List[str]:
        """Parse comma-separated allowed emails."""
        return [
            e.strip()
            for e in self.allowed_emails.split(",")
            if e.strip()
        ]

    class Config:
        env_file = ".env"
        populate_by_name = True


# ─────────────────────────────────────────────────────────────────────────────
# Singleton accessor
# ─────────────────────────────────────────────────────────────────────────────

_config: Optional[NovaSellConfig] = None


def get_config() -> NovaSellConfig:
    """Get or create the global NovaSell configuration."""
    global _config
    if _config is None:
        _config = NovaSellConfig()
    return _config


def refresh_config() -> NovaSellConfig:
    """Force reload configuration from environment."""
    global _config
    _config = NovaSellConfig()
    return _config