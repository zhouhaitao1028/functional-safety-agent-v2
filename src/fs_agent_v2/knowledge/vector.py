from __future__ import annotations

from collections import Counter
from hashlib import sha256
from math import sqrt
from typing import Protocol
from urllib import request
import re
import json


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    output: list[str] = []
    for match in TOKEN_PATTERN.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            output.extend(match)
            output.extend(match[index : index + 2] for index in range(len(match) - 1))
        else:
            output.append(match)
    return output


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str: ...

    def embed(self, text: str) -> list[float]: ...


class HashingEmbeddingProvider:
    """Dependency-free development embedding; replaceable by a semantic model adapter."""

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    @property
    def name(self) -> str:
        return f"hashing-development-v1-{self.dimensions}"

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token, count in Counter(tokenize(text)).items():
            digest = sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign * float(count)
        norm = sqrt(sum(value * value for value in vector))
        if norm:
            return [value / norm for value in vector]
        return vector


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return f"openai-compatible:{self.base_url}:{self.model}"

    def embed(self, text: str) -> list[float]:
        endpoint = (
            self.base_url
            if self.base_url.endswith("/embeddings")
            else self.base_url + "/embeddings"
        )
        payload = json.dumps({"model": self.model, "input": text}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        call = request.Request(endpoint, data=payload, headers=headers, method="POST")
        with request.urlopen(call, timeout=self.timeout_seconds) as response:
            value = json.loads(response.read().decode("utf-8"))
        return [float(item) for item in value["data"][0]["embedding"]]


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return max(0.0, sum(a * b for a, b in zip(left, right)))


def lexical_score(query: str, content: str) -> float:
    query_tokens = Counter(tokenize(query))
    if not query_tokens:
        return 0.0
    content_tokens = Counter(tokenize(content))
    matched = sum(min(count, content_tokens[token]) for token, count in query_tokens.items())
    return matched / sum(query_tokens.values())
