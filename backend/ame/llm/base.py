from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

from ame.config import get_settings
from ame.costs.tracker import record_cost_sync

T = TypeVar("T", bound=BaseModel)


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    async def generate_structured(
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        raise NotImplementedError

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class DevLLMProvider(LLMProvider):
    name = "dev"

    async def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        record_cost_sync("dev", "dev-local", 0, 0, 0.0, agent="llm")
        digest = hashlib.sha256((system or "" + prompt).encode()).hexdigest()[:12]
        return f"DEV_RESPONSE:{digest}"

    async def generate_structured(
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        record_cost_sync("dev", "dev-local", 0, 0, 0.0, agent="llm")
        return _dev_structured(schema, prompt)

    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 for b in digest[:16]]


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY required for non-dev provider")
        self.settings = settings

    async def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        import httpx

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": self.settings.llm_model, "messages": messages}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.settings.llm_base_url or 'https://api.openai.com/v1'}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        usage = data.get("usage", {})
        record_cost_sync(
            self.name,
            self.settings.llm_model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            _estimate_cost(usage),
            agent="llm",
        )
        return data["choices"][0]["message"]["content"]

    async def generate_structured(
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        text = await self.generate_text(
            f"{prompt}\nReturn only JSON matching this schema: {schema.model_json_schema()}",
            system=system,
        )
        return schema.model_validate_json(_extract_json(text))

    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 for b in digest[:16]]


def _estimate_cost(usage: dict[str, Any]) -> float:
    prompt = float(usage.get("prompt_tokens", 0))
    completion = float(usage.get("completion_tokens", 0))
    return (prompt * 0.000002) + (completion * 0.000008)


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("structured output missing JSON")
    return text[start : end + 1]


def _dev_structured(schema: type[T], prompt: str) -> T:
    name = schema.__name__
    if name == "ResearchPackOut":
        return schema.model_validate(
            {
                "topic": _topic_from_prompt(prompt),
                "summary": "Development research pack assembled from permitted public signals.",
                "claims": [
                    {
                        "claim": "Public discussion of this topic is currently elevated.",
                        "kind": "reasonable_interpretation",
                        "sources": ["https://news.ycombinator.com"],
                        "freshness_checked": True,
                        "stale": False,
                        "publishable": True,
                    }
                ],
                "source_urls": ["https://news.ycombinator.com"],
                "uncertain_claims": [],
                "unsuitable_claims": [],
                "confidence": 0.72,
            }
        )
    if name == "ScriptCandidate":
        topic = _topic_from_prompt(prompt)
        return schema.model_validate(
            {
                "hook": f"The overlooked reason {topic} suddenly matters.",
                "body": (
                    f"{topic} is moving from lab curiosity to operational systems. "
                    "Three public signals explain the shift: capability, cost, and deployment."
                ),
                "reveal": "The constraint is no longer the model. It is integration.",
                "cta": "Follow for the next verified build note.",
                "estimated_duration": 38,
                "on_screen_text": ["THE SHIFT", topic.upper()[:42], "INTEGRATION"],
                "scene_plan": [
                    {"at": 0, "text": "THE SHIFT", "duration": 3},
                    {"at": 3, "text": topic[:48], "duration": 12},
                    {"at": 15, "text": "Capability. Cost. Deployment.", "duration": 12},
                    {"at": 27, "text": "The constraint is integration.", "duration": 11},
                ],
                "voice_style": "clear_authoritative",
                "caption": f"{topic}: why the constraint moved.",
                "hashtags": ["technology", "engineering", "future"],
                "sources_used": ["https://news.ycombinator.com"],
                "claims": [
                    {
                        "claim": "Public technical discussion of the topic is elevated.",
                        "kind": "reasonable_interpretation",
                        "sources": ["https://news.ycombinator.com"],
                        "publishable": True,
                    }
                ],
            }
        )
    fields = {}
    for key, field in schema.model_fields.items():
        annotation = field.annotation
        if annotation is str:
            fields[key] = f"dev-{key}"
        elif annotation is int:
            fields[key] = 1
        elif annotation is float:
            fields[key] = 0.5
        elif annotation is bool:
            fields[key] = False
        else:
            fields[key] = [] if "list" in str(annotation) else {}
    return schema.model_validate(fields)


def _topic_from_prompt(prompt: str) -> str:
    for line in prompt.splitlines():
        if "topic" in line.lower():
            return line.split(":", 1)[-1].strip()[:80] or "emerging technology"
    return "emerging technology"


def get_llm() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider in {"openai", "compatible"} and settings.llm_api_key:
        return OpenAICompatibleProvider()
    return DevLLMProvider()
