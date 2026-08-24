from __future__ import annotations

import os

from .config import CapabilityConfigurationError, CapabilitySettings
from .http_provider import GenericHTTPProvider
from .providers import DisabledProvider, FixedResponseProvider


def provider_for(capability: str):
    settings = CapabilitySettings.from_environment(capability)
    if settings.provider == "disabled":
        return DisabledProvider()
    if settings.provider == "fixed":
        environment = os.environ.get("FIRE_INSPECT_ENV", "development").lower()
        allowed = os.environ.get("FIRE_INSPECT_ALLOW_FIXED_AI", "0") == "1"
        if environment == "production" or not allowed:
            raise CapabilityConfigurationError("固定 AI 仅可在显式启用的非生产验收环境使用")
        return FixedResponseProvider()
    return GenericHTTPProvider(settings)
