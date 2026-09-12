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
        )
