"""HTTP client for an OpenAI-compatible embeddings endpoint.

Used at QUERY time so Fundus can apply a model-specific instruct prefix to the
query (documents are embedded by Meilisearch at index time). The resulting query
vector is handed to Meilisearch directly, keeping the keyword query clean.
"""

from __future__ import annotations

import httpx


class EmbeddingClient:
    def __init__(
        self,
        url: str,
        model: str,
        *,
        api_key: str | None = None,
        query_prompt: str = "",
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._url = url
        self._model = model
        self._api_key = api_key
        self._query_prompt = query_prompt
        self._client = client or httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def embed(self, inputs: list[str]) -> list[list[float]]:
        resp = self._client.post(
            self._url, headers=self._headers(), json={"model": self._model, "input": inputs}
        )
        resp.raise_for_status()
        payload = resp.json()
        result: list[list[float]] = [item["embedding"] for item in payload["data"]]
        return result

    def embed_query(self, query: str) -> list[float]:
        text = f"{self._query_prompt}{query}" if self._query_prompt else query
        return self.embed([text])[0]
