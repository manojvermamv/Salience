"""Validated process configuration with secret references only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class ConfigurationError(ValueError):
    """Raised when a required process setting is missing or invalid."""


@dataclass(frozen=True, slots=True)
class SecretReference:
    """A non-secret pointer resolved by a scoped secret resolver later."""

    uri: str

    def __post_init__(self) -> None:
        if not self.uri.startswith("env://"):
            raise ConfigurationError("secret references must use env:// URIs")


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings safe to retain because they contain no secret values."""

    database_url: str
    temporal_target: str
    object_store_endpoint: str
    worker_task_queue: str
    mock_effect_provider_url: str
    control_plane_token: SecretReference
    model_runtime_id: str | None
    model_base_url: str | None
    model_name: str | None
    model_secret_ref: SecretReference | None
    research_allowed_domains: tuple[str, ...]
    research_rss_feed_urls: tuple[str, ...]
    research_request_timeout_seconds: int
    research_max_response_bytes: int
    browser_enabled: bool
    browser_timeout_seconds: int
    browser_step_limit: int
    mcp_endpoint: str | None
    mcp_auth_secret_ref: SecretReference | None
    a2a_endpoint: str | None
    a2a_auth_secret_ref: SecretReference | None

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Settings":
        token = values.get("CONTROL_PLANE_TOKEN", "").strip()
        if not token:
            raise ConfigurationError("CONTROL_PLANE_TOKEN must be non-empty")
        return cls(
            database_url=values.get(
                "DATABASE_URL",
                "postgresql+asyncpg://salience:salience@postgres:5432/salience",
            ),
            temporal_target=values.get("TEMPORAL_TARGET", "temporal:7233"),
            object_store_endpoint=values.get("OBJECT_STORE_ENDPOINT", "http://garage:3900"),
            worker_task_queue=values.get("WORKER_TASK_QUEUE", "salience-phase-one"),
            mock_effect_provider_url=values.get(
                "MOCK_EFFECT_PROVIDER_URL", "http://mock-effect-provider:8081"
            ),
            control_plane_token=SecretReference("env://CONTROL_PLANE_TOKEN"),
            model_runtime_id=_optional_value(values, "MODEL_RUNTIME_ID"),
            model_base_url=_optional_value(values, "MODEL_BASE_URL"),
            model_name=_optional_value(values, "MODEL_NAME"),
            model_secret_ref=_optional_secret_reference(values, "MODEL_SECRET_REF"),
            research_allowed_domains=_domain_list(values),
            research_rss_feed_urls=_url_list(values, "RESEARCH_RSS_FEED_URLS"),
            research_request_timeout_seconds=_positive_integer(
                values, "RESEARCH_REQUEST_TIMEOUT_SECONDS", default=15
            ),
            research_max_response_bytes=_positive_integer(
                values, "RESEARCH_MAX_RESPONSE_BYTES", default=1_000_000
            ),
            browser_enabled=_boolean(values, "BROWSER_ENABLED", default=False),
            browser_timeout_seconds=_positive_integer(
                values, "BROWSER_TIMEOUT_SECONDS", default=30
            ),
            browser_step_limit=_positive_integer(values, "BROWSER_STEP_LIMIT", default=20),
            mcp_endpoint=_optional_value(values, "MCP_ENDPOINT"),
            mcp_auth_secret_ref=_optional_secret_reference(values, "MCP_AUTH_SECRET_REF"),
            a2a_endpoint=_optional_value(values, "A2A_ENDPOINT"),
            a2a_auth_secret_ref=_optional_secret_reference(values, "A2A_AUTH_SECRET_REF"),
        )


def _optional_value(values: Mapping[str, str], name: str) -> str | None:
    value = values.get(name, "").strip()
    return value or None


def _optional_secret_reference(
    values: Mapping[str, str], name: str
) -> SecretReference | None:
    value = _optional_value(values, name)
    return SecretReference(value) if value else None


def _domain_list(values: Mapping[str, str]) -> tuple[str, ...]:
    raw_domains = values.get("RESEARCH_ALLOWED_DOMAINS", "")
    return tuple(domain for value in raw_domains.split(",") if (domain := value.strip()))


def _url_list(values: Mapping[str, str], name: str) -> tuple[str, ...]:
    urls = tuple(value.strip() for value in values.get(name, "").split(",") if value.strip())
    if any(not url.startswith("https://") for url in urls):
        raise ConfigurationError(f"{name} must contain only https URLs")
    return urls


def _positive_integer(values: Mapping[str, str], name: str, *, default: int) -> int:
    raw_value = values.get(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a positive integer") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _boolean(values: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw_value = values.get(name, str(default)).strip().lower()
    if raw_value in {"true", "1", "yes"}:
        return True
    if raw_value in {"false", "0", "no"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean")
