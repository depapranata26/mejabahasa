"""Swappable model-provider interface for MejaBahasa.

The agent planner talks to this interface, never directly to a vendor SDK.
Primary provider: Baseten (OpenAI-compatible chat completions) serving the
Meta open-weight model ``meta-models/muse-spark-open-1026``.

Credentials are read from the environment at runtime only. They are never
hardcoded, never logged, and never committed (see .env.example and
.gitignore).
"""

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod


class ModelProvider(ABC):
    """Minimal chat-completion interface the agent planner depends on."""

    @property
    @abstractmethod
    def name(self):
        """Human-readable provider identifier, e.g. 'baseten:<model>'."""

    @abstractmethod
    def complete(self, messages, temperature=0.2, max_tokens=1024):
        """Return the assistant message text for a list of chat messages.

        ``messages`` is a list of ``{"role": ..., "content": ...}`` dicts.
        """


class BasetenProvider(ModelProvider):
    """Baseten inference (OpenAI-compatible) for Muse Spark Open."""

    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or os.environ.get("BASETEN_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "BASETEN_API_KEY is not set. Copy .env.example to .env and "
                "fill in your own key; never commit .env."
            )
        self.base_url = (
            base_url
            or os.environ.get(
                "BASETEN_BASE_URL", "https://inference.baseten.co/v1"
            )
        ).rstrip("/")
        self.model = (
            model
            or os.environ.get("MODEL", "meta-models/muse-spark-open-1026")
        )

    @property
    def name(self):
        return f"baseten:{self.model}"

    def complete(self, messages, temperature=0.2, max_tokens=1024):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"Baseten API error {exc.code}: {detail}") from exc
        return data["choices"][0]["message"]["content"]


def get_provider(name=None):
    """Factory: pick a provider by name (env PROVIDER, default 'baseten').

    Planned fallbacks share this interface: 'fireworks' and 'meta' raise
    NotImplementedError until their adapters land.
    """
    name = (name or os.environ.get("PROVIDER", "baseten")).lower()
    if name == "baseten":
        return BasetenProvider()
    raise NotImplementedError(
        f"provider '{name}' is not implemented yet "
        "(planned fallbacks: fireworks, meta)"
    )
