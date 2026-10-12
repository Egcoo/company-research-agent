"""Load explicit runtime configuration without opening local credential files."""

import os
import threading
import time
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class ConfigurationError(ValueError):
    """Describe a missing or unsupported runtime configuration."""


class ResearchError(RuntimeError):
    """Carry a safe, structured failure across research boundaries."""

    def __init__(self, code: str, message: str, *, details: dict | None = None):
        """Store a machine-readable code and a credential-free message."""
        self.code = code
        self.details = details
        super().__init__(message)


class Settings(BaseModel):
    """Hold one research run's configuration and resource limits."""

    model: str = "anthropic:claude-sonnet-4-5-20250929"
    anthropic_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    tavily_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    langsmith_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    sec_user_agent: str = ""
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    model_timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_requests: int = Field(default=30, ge=1, le=100)
    max_tool_calls: int = Field(default=50, ge=1, le=150)
    max_model_calls: int = Field(default=30, ge=1, le=100)
    max_output_tokens: int = Field(default=8192, ge=1024, le=16384)
    structured_thinking: Literal["provider_default", "disabled"] = "provider_default"
    deadline_seconds: float = Field(default=300, gt=0, le=1800)
    max_response_bytes: int = Field(default=5_000_000, ge=1024, le=20_000_000)
    max_content_chars: int = Field(default=24_000, ge=500, le=100_000)
    max_search_results: int = Field(default=5, ge=1, le=10)
    max_retries: int = Field(default=1, ge=0, le=2)
    sec_interval_seconds: float = Field(default=0.25, ge=0.11)
    tracing: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Read supported variables without opening .env or constructing clients."""
        values = os.environ if environ is None else environ
        return cls(
            model=values.get("DEEP_EQUITY_MODEL", cls.model_fields["model"].default),
            anthropic_api_key=values.get("ANTHROPIC_API_KEY") or None,
            tavily_api_key=values.get("TAVILY_API_KEY") or None,
            langsmith_api_key=values.get("LANGSMITH_API_KEY") or None,
            sec_user_agent=values.get("SEC_USER_AGENT", ""),
            tracing=values.get("LANGSMITH_TRACING", "false").lower() == "true",
            max_requests=values.get("DEEP_EQUITY_MAX_REQUESTS", 30),
            max_tool_calls=values.get("DEEP_EQUITY_MAX_TOOL_CALLS", 50),
            max_model_calls=values.get("DEEP_EQUITY_MAX_MODEL_CALLS", 30),
            max_output_tokens=values.get("DEEP_EQUITY_MAX_OUTPUT_TOKENS", 8192),
            structured_thinking=values.get(
                "DEEP_EQUITY_STRUCTURED_THINKING", "provider_default"
            ),
            model_timeout_seconds=values.get("DEEP_EQUITY_MODEL_TIMEOUT_SECONDS", 120),
            deadline_seconds=values.get("DEEP_EQUITY_DEADLINE_SECONDS", 300),
        )

    def require(self, provider: str) -> str:
        """Require a configured provider before any real external request."""
        name, secret = {
            "anthropic": ("ANTHROPIC_API_KEY", self.anthropic_api_key),
            "tavily": ("TAVILY_API_KEY", self.tavily_api_key),
            "langsmith": ("LANGSMITH_API_KEY", self.langsmith_api_key),
        }[provider]
        value = secret.get_secret_value().strip() if secret else ""
        if not value or value.startswith(("your_", "lsv2_pt_your_")):
            raise ConfigurationError(
                f"当前需要你手动完成 {provider} API Key 配置（{name}）。"
            )
        return value

    def require_sec_user_agent(self) -> str:
        """Require the application's declared SEC contact before accessing EDGAR."""
        if "@" not in self.sec_user_agent or " " not in self.sec_user_agent.strip():
            raise ConfigurationError(
                "当前需要你手动配置 SEC_USER_AGENT：应用名称和联系邮箱。"
            )
        return self.sec_user_agent


class ResearchBudget:
    """Share bounded counters and a deadline across one run's workers."""

    def __init__(self, settings: Settings):
        """Start a fresh budget, never reused across research runs."""
        self.settings = settings
        self.started_at = time.monotonic()
        self.counts = {"request": 0, "tool": 0, "model": 0}
        self._lock = threading.Lock()

    def check(self) -> None:
        """Reject further work once the research deadline is reached."""
        if time.monotonic() - self.started_at >= self.settings.deadline_seconds:
            raise ResearchError("budget_exceeded", "Research deadline exceeded.")

    def remaining(self) -> dict[str, int | float]:
        """Read one consistent budget snapshot without consuming any calls."""
        with self._lock:
            return {
                "request": self.settings.max_requests - self.counts["request"],
                "tool": self.settings.max_tool_calls - self.counts["tool"],
                "model": self.settings.max_model_calls - self.counts["model"],
                "seconds": max(
                    0.0,
                    self.settings.deadline_seconds
                    - (time.monotonic() - self.started_at),
                ),
            }

    def consume(self, kind: str, *, reserve: int = 0) -> None:
        """Account for a call atomically, optionally reserving final synthesis steps."""
        with self._lock:
            self.check()
            maximum = {
                "request": self.settings.max_requests,
                "tool": self.settings.max_tool_calls,
                "model": self.settings.max_model_calls,
            }[kind]
            if self.counts[kind] >= maximum:
                raise ResearchError(
                    "budget_exceeded", f"Research {kind} limit exceeded."
                )
            if not 0 <= reserve < maximum:
                raise ValueError("Reserved calls must be within the configured limit.")
            if reserve and self.counts[kind] >= maximum - reserve:
                raise ResearchError(
                    "synthesis_reserved",
                    "Remaining model steps are reserved for the orchestrator's final report.",
                )
            self.counts[kind] += 1
