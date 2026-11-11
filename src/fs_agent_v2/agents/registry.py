from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import json
import os
import re

from ..errors import ContractError


@dataclass(frozen=True)
class ApiDefinition:
    default_provider: str
    default_model: str
    provider_env: str
    base_url_env: str
    api_key_env: str
    model_env: str
    temperature: float
    timeout_seconds: int

    @classmethod
    def from_dict(cls, value: dict) -> "ApiDefinition":
        return cls(
            default_provider=value.get("default_provider", "openai_compatible"),
            default_model=value.get("default_model", ""),
            provider_env=value["provider_env"],
            base_url_env=value["base_url_env"],
            api_key_env=value["api_key_env"],
            model_env=value["model_env"],
            temperature=float(value.get("temperature", 0.1)),
            timeout_seconds=int(value.get("timeout_seconds", 180)),
        )


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    title: str
    role: str
    kind: str
    business: str
    consumes: tuple[str, ...]
    optional_consumes: tuple[str, ...]
    produces: tuple[str, ...]
    output_schema: str | None
    knowledge_scopes: tuple[str, ...]
    skills: tuple[str, ...]
    tools: tuple[str, ...]
    deterministic_checks: tuple[str, ...]
    approval_required: bool
    api: ApiDefinition | None

    @classmethod
    def from_dict(cls, value: dict) -> "AgentDefinition":
        required = {"id", "title", "role", "kind", "business", "consumes", "produces"}
        missing = required - value.keys()
        if missing:
            raise ContractError(f"agent config is missing fields: {sorted(missing)}")
        api_value = value.get("api")
        return cls(
            id=value["id"],
            title=value["title"],
            role=value["role"],
            kind=value["kind"],
            business=value["business"],
            consumes=tuple(value["consumes"]),
            optional_consumes=tuple(value.get("optional_consumes", [])),
            produces=tuple(value["produces"]),
            output_schema=value.get("output_schema"),
            knowledge_scopes=tuple(value.get("knowledge_scopes", [])),
            skills=tuple(value.get("skills", [])),
            tools=tuple(value.get("tools", [])),
            deterministic_checks=tuple(value.get("deterministic_checks", [])),
            approval_required=bool(value.get("approval_required", True)),
            api=ApiDefinition.from_dict(api_value) if api_value else None,
        )


@dataclass(frozen=True)
class ResolvedApiConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    timeout_seconds: int


@dataclass(frozen=True)
class StoredSkill:
    id: str
    path: Path
    instructions: str


class AgentRegistry:
    """Loads business capabilities independently from orchestration and providers."""

    _FORBIDDEN_SECRET_KEYS = {"api_key", "token", "access_token", "secret"}

    def __init__(self, root: Path, skill_root: Path | None = None):
        self.root = root
        self.skill_root = skill_root or root.parents[1] / "skills"
        self._agents = self._load()

    def list(self) -> tuple[AgentDefinition, ...]:
        return tuple(sorted(self._agents.values(), key=lambda item: item.id))

    def get(self, agent_id: str) -> AgentDefinition:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise ContractError(f"unknown business agent: {agent_id}") from exc

    def resolve_api(
        self, agent_id: str, environ: Mapping[str, str] | None = None
    ) -> ResolvedApiConfig:
        agent = self.get(agent_id)
        if agent.api is None:
            raise ContractError(f"agent {agent_id} is deterministic and has no model API")
        source = environ if environ is not None else os.environ
        api = agent.api
        local = self._local_api_values(agent_id)
        provider = source.get(api.provider_env) or local.get("provider") or api.default_provider
        base_url = source.get(api.base_url_env) or local.get("base_url", "")
        api_key = source.get(api.api_key_env) or local.get("api_key", "")
        model = source.get(api.model_env) or local.get("model") or api.default_model
        missing = []
        if not model:
            missing.append(api.model_env)
        if not api_key:
            missing.append(api.api_key_env)
        if missing:
            raise ContractError(
                f"agent {agent_id} API environment is incomplete: {', '.join(missing)}"
            )
        return ResolvedApiConfig(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=api.temperature,
            timeout_seconds=api.timeout_seconds,
        )

    def api_status(
        self, agent_id: str, environ: Mapping[str, str] | None = None
    ) -> dict:
        agent = self.get(agent_id)
        if agent.api is None:
            return {"agent_id": agent.id, "api_required": False}
        source = environ if environ is not None else os.environ
        api = agent.api
        local = self._local_api_values(agent_id)
        fields = {
            "provider": (api.provider_env, "provider", api.default_provider),
            "base_url": (api.base_url_env, "base_url", ""),
            "api_key": (api.api_key_env, "api_key", ""),
            "model": (api.model_env, "model", api.default_model),
        }
        configured = {}
        value_sources = {}
        for field, (env_name, local_name, default) in fields.items():
            if source.get(env_name):
                configured[field] = True
                value_sources[field] = "environment"
            elif local.get(local_name):
                configured[field] = True
                value_sources[field] = "config/local/model_runtime.json"
            else:
                configured[field] = bool(default)
                value_sources[field] = "agent_default" if default else "missing"
        return {
            "agent_id": agent.id,
            "api_required": True,
            "configured": configured,
            "sources": value_sources,
        }

    def skill_documents(self, agent_id: str) -> tuple[StoredSkill, ...]:
        agent = self.get(agent_id)
        result = []
        for skill_id in agent.skills:
            if not re.fullmatch(r"[a-z0-9-]{1,64}", skill_id):
                raise ContractError(f"agent {agent_id} has unsafe skill id: {skill_id}")
            path = self.skill_root / skill_id / "SKILL.md"
            if not path.exists():
                raise ContractError(f"agent {agent_id} references missing skill: {skill_id}")
            result.append(
                StoredSkill(
                    id=skill_id,
                    path=path,
                    instructions=path.read_text(encoding="utf-8"),
                )
            )
        return tuple(result)

    def _load(self) -> dict[str, AgentDefinition]:
        agents: dict[str, AgentDefinition] = {}
        for path in sorted(self.root.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            self._reject_plaintext_secrets(value, path)
            agent = AgentDefinition.from_dict(value)
            if agent.id in agents:
                raise ContractError(f"duplicate agent id: {agent.id}")
            agents[agent.id] = agent
        if not agents:
            raise ContractError(f"no agent configs found under {self.root}")
        for agent in agents.values():
            self.skill_documents_for_definition(agent)
        return agents

    def skill_documents_for_definition(
        self, agent: AgentDefinition
    ) -> tuple[StoredSkill, ...]:
        result = []
        for skill_id in agent.skills:
            if not re.fullmatch(r"[a-z0-9-]{1,64}", skill_id):
                raise ContractError(f"agent {agent.id} has unsafe skill id: {skill_id}")
            path = self.skill_root / skill_id / "SKILL.md"
            if not path.exists():
                raise ContractError(f"agent {agent.id} references missing skill: {skill_id}")
            result.append(StoredSkill(skill_id, path, path.read_text(encoding="utf-8")))
        return tuple(result)

    def _local_api_values(self, agent_id: str) -> dict:
        path = self.root.parent / "local" / "model_runtime.json"
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"invalid local model runtime config: {exc}") from exc
        defaults = value.get("defaults", {})
        overrides = value.get("agents", {}).get(agent_id, {})
        if not isinstance(defaults, dict) or not isinstance(overrides, dict):
            raise ContractError("local model runtime defaults/agent override must be objects")
        return {**defaults, **overrides}

    @classmethod
    def _reject_plaintext_secrets(cls, value: object, path: Path) -> None:
        if isinstance(value, dict):
            forbidden = cls._FORBIDDEN_SECRET_KEYS & set(value)
            if forbidden:
                raise ContractError(
                    f"plaintext secret fields {sorted(forbidden)} are forbidden in {path.name}"
                )
            for child in value.values():
                cls._reject_plaintext_secrets(child, path)
        elif isinstance(value, list):
            for child in value:
                cls._reject_plaintext_secrets(child, path)
