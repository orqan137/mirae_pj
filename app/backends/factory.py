from __future__ import annotations

from app.backends.demo import DemoBackend
from app.backends.hcx import HyperClovaXBackend
from app.backends.http import HttpAgentBackend
from app.config import Settings
from app.service import AgentBackend


def build_backend(settings: Settings) -> AgentBackend:
    if settings.agent_backend == "demo":
        return DemoBackend()
    if settings.agent_backend == "http":
        return HttpAgentBackend(settings)
    if settings.agent_backend == "hcx":
        return HyperClovaXBackend(settings)
    raise ValueError(f"Unsupported Agent backend: {settings.agent_backend}")
