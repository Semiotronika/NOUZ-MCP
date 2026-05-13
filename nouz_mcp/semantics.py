"""LLM and embedding client helpers for NOUZ semantics."""

import hashlib
import logging
import re
from typing import Awaitable, Callable, List, MutableMapping, Optional

import aiohttp


Logger = logging.Logger
CallLLM = Callable[[str], Awaitable[str]]


async def call_llm(prompt: str, api_url: str, model: str, logger: Optional[Logger] = None) -> str:
    """Call an OpenAI-compatible chat completion endpoint."""
    try:
        url = f"{api_url}/chat/completions"
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 500,
        }
        if model:
            payload["model"] = model
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        if logger:
            logger.warning(f"LLM unavailable: {exc}")
        return ""


def clean_tag_result(result: str) -> List[str]:
    """Parse a comma/newline separated LLM keyword response into clean tags."""
    if not result:
        return []

    tags = [item.strip().lower().lstrip("#") for item in result.replace("\n", ",").split(",") if item.strip()]
    clean_tags = []
    for tag in tags:
        tag = re.sub(r"^(here|keywords|tags|terms|words).*?:", "", tag).strip().lstrip("#")
        if tag and 2 < len(tag) < 50:
            clean_tags.append(tag)
    return list(set(clean_tags))[:5]


async def extract_tags(content: str, llm_model: str, call_llm_func: CallLLM) -> List[str]:
    """Extract 3-5 tags from content through a provided LLM caller."""
    if not content or not llm_model:
        return []
    prompt = (
        "Extract 3-5 keywords from this text. Return them as a comma-separated list "
        f"without hashtags or numbers.\n\nText: {content[:2000]}"
    )
    result = await call_llm_func(prompt)
    return clean_tag_result(result)


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
