from __future__ import annotations

import os
from dataclasses import dataclass


class CapabilityConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class CapabilitySettings:
    capability: str
    provider: str
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: float = 60

    def __repr__(self) -> str:
        return (
            "CapabilitySettings("
            f"capability={self.capability!r}, provider={self.provider!r}, "
            f"endpoint={self.endpoint!r}, model={self.model!r}, api_key=<redacted>, "
            f"timeout_seconds={self.timeout_seconds!r})"
        )

    @classmethod
    def from_environment(cls, capability: str) -> CapabilitySettings:
        prefix = f"FIRE_INSPECT_{capability.upper()}"
        provider = os.environ.get(f"{prefix}_PROVIDER", "disabled").strip().lower()
        endpoint = os.environ.get(f"{prefix}_ENDPOINT", "").strip()
        model = os.environ.get(f"{prefix}_MODEL", "").strip()
        api_key = os.environ.get(f"{prefix}_API_KEY", "")
        timeout_raw = os.environ.get(f"{prefix}_TIMEOUT", "60")
        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise CapabilityConfigurationError(f"{capability} 超时配置无效") from exc
        if provider == "http" and (not endpoint or not model or not api_key):
            raise CapabilityConfigurationError(f"{capability} HTTP 适配器配置不完整")
        if provider not in {"disabled", "fixed", "http"}:
            raise CapabilityConfigurationError(f"{capability} 适配器不受支持")
        return cls(capability, provider, endpoint, model, api_key, timeout)
