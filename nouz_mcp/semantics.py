"""LLM and embedding client helpers for NOUZ semantics."""

import hashlib
import logging
from typing import List, MutableMapping, Optional

import aiohttp


Logger = logging.Logger


async def get_embedding(
    text: str,
    *,
    enabled: bool,
    provider: str,
    model: str,
    api_url: str,
    api_key: str,
    cache: MutableMapping[str, List[float]],
    logger: Optional[Logger] = None,
    max_cache_size: int = 500,
) -> List[float]:
    """Return an embedding vector from Ollama or an OpenAI-compatible endpoint."""
    if not enabled:
        return []

    cache_key = hashlib.md5(text.encode("utf-8")).hexdigest()
    if cache_key in cache:
        return cache[cache_key]

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        if provider == "ollama":
            url = f"{api_url}/api/embeddings"
            payload = {"model": model or "nomic-embed-text", "prompt": text}
        else:
            url = f"{api_url}/embeddings"
            payload = {"input": text, "model": model} if model else {"input": text}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

                if provider == "ollama":
                    vec = data.get("embedding", [])
                else:
                    vec = data["data"][0]["embedding"]

                cache[cache_key] = vec
                if len(cache) > max_cache_size:
                    cache.pop(next(iter(cache)))
                return vec
    except Exception as exc:
        if logger:
            logger.warning(f"Embeddings unavailable ({provider}): {exc}")
        return []
